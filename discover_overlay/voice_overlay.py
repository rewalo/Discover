#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""Overlay window for voice"""
import random
import gettext
import logging
import math
import sys
import locale
from time import perf_counter
import cairo
import pkg_resources
from .overlay import OverlayWindow
from .image_getter import get_surface, draw_img_to_rect, draw_img_to_mask
# pylint: disable=wrong-import-order
import gi
gi.require_version('PangoCairo', '1.0')
# pylint: disable=wrong-import-position,wrong-import-order
from gi.repository import Pango, PangoCairo, GLib  # nopep8

log = logging.getLogger(__name__)

t = gettext.translation(
    'default', pkg_resources.resource_filename('discover_overlay', 'locales'), fallback=True)
_ = t.gettext


class VoiceOverlayWindow(OverlayWindow):
    """Overlay window for voice"""

    def __init__(self, discover, piggyback=None):
        OverlayWindow.__init__(self, discover, piggyback)

        self.avatars = {}
        self.avatar_masks = {}

        self.dummy_data = []
        mostly_false = [False, False, False, False, False, False, False, True]
        for i in range(0, 100):
            speaking = mostly_false[random.randint(0, 7)]
            scream = ''
            if random.randint(0, 20) == 2:
                scream = random.randint(8, 15)*'a'
            name = f"Player {i} {scream}"
            self.dummy_data.append({
                "id": i,
                "username": name,
                "avatar": None,
                "deaf": mostly_false[random.randint(0, 7)],
                "mute": mostly_false[random.randint(0, 7)],
                "speaking": speaking,
                'lastspoken': random.randint(2000, 2100) if speaking else random.randint(10, 30),
                'friendlyname': name,
            })
        self.show_avatar = True
        self.avatar_size = 48
        self.nick_length = 32
        self.text_pad = 6
        self.text_font = None
        self.title_font = None
        self.text_size = 13
        self.text_baseline_adj = 0
        self.icon_spacing = 8
        self.vert_edge_padding = 0
        self.horz_edge_padding = 0
        self.only_speaking = None
        self.highlight_self = None
        self.order = None
        self.def_avatar = None
        self.def_avatar_mask = None
        self.channel_icon = None
        self.channel_mask = None
        self.channel_icon_url = None
        self.overflow = None
        self.use_dummy = False
        self.dummy_count = 10
        self.show_title = True
        self.show_connection = True
        self.show_disconnected = True
        self.channel_title = ""
        self.border_width = 2
        self.icon_transparency = 0.0
        self.fancy_border = False
        self.only_speaking_grace_period = 0

        self.fade_out_inactive = True
        self.fade_out_limit = 0.1
        self.inactive_time = 10  # Seconds
        self.inactive_fade_time = 20  # Seconds
        self.fade_opacity = 1.0
        self.fade_start = 0

        self.inactive_timeout = None
        self.fadeout_timeout = None

        self.round_avatar = True
        self.rounded_names = True
        self.separate_names = False
        self.icon_only = True
        self.talk_col = [0.0, 0.6, 0.0, 0.1]
        self.text_col = [1.0, 1.0, 1.0, 1.0]
        self.text_hili_col = [1.0, 1.0, 1.0, 1.0]
        self.norm_col = [0.0, 0.0, 0.0, 0.5]
        self.wind_col = [0.0, 0.0, 0.0, 0.0]
        self.mute_col = [0.7, 0.0, 0.0, 1.0]
        self.mute_bg_col = [0.0, 0.0, 0.0, 0.5]
        self.hili_col = [0.0, 0.0, 0.0, 0.9]
        self.border_col = [0.0, 0.0, 0.0, 0.0]
        self.avatar_bg_col = [0.0, 0.0, 1.0, 1.0]
        self.userlist = []
        self.connection_status = "DISCONNECTED"
        self.horizontal = False
        self.guild_ids = tuple()
        self.force_location()
        get_surface(self.recv_avatar,
                    "discover-overlay-default",
                    'def', self.avatar_size)
        self.set_title("Discover Voice")
        self.redraw()

    def reset_action_timer(self):
        """Reset time since last voice activity"""
        self.fade_opacity = 1.0

        # Remove both fading-out effect and timer set last time this happened
        if self.inactive_timeout:
            GLib.source_remove(self.inactive_timeout)
            self.inactive_timeout = None
        if self.fadeout_timeout:
            GLib.source_remove(self.fadeout_timeout)
            self.fadeout_timeout = None

        # If we're using this feature, schedule a new inactivity timer
        if self.fade_out_inactive:
            self.inactive_timeout = GLib.timeout_add_seconds(
                self.inactive_time, self.overlay_inactive)

    def overlay_inactive(self):
        """Timed callback when inactivity limit is hit"""
        self.fade_start = perf_counter()
        # Fade out in 200 steps over X seconds.
        self.fadeout_timeout = GLib.timeout_add(
            self.inactive_fade_time/200 * 1000, self.overlay_fadeout)
        self.inactive_timeout = None
        return False

    def overlay_fadeout(self):
        """Repeated callback after inactivity started"""
        self.set_needs_redraw()
        # There's no guarantee over the granularity of the callback here,
        # so use our time-since to work out how faded out we should be
        # Might look choppy on systems under high cpu usage but that's just how it's going to be
        now = perf_counter()
        time_percent = (now - self.fade_start) / self.inactive_fade_time
        if time_percent >= 1.0:
            self.fade_opacity = self.fade_out_limit
            self.fadeout_timeout = None
            return False

        self.fade_opacity = self.fade_out_limit + \
            ((1.0 - self.fade_out_limit) * (1.0 - time_percent))
        return True

    def col(self, col, alpha=1.0):
        """Convenience function to set the cairo context next colour.
         Altered to account for fade-out function"""
        if alpha is None:
            self.context.set_source_rgba(col[0], col[1], col[2], col[3])
        else:
            self.context.set_source_rgba(
                col[0], col[1], col[2], col[3] * alpha *
                self.fade_opacity * self.icon_transparency)

    def set_icon_transparency(self, trans):
        """Config option: overall voice overlay opacity"""
        if self.icon_transparency != trans:
            self.icon_transparency = trans
            self.set_needs_redraw()

    def set_blank(self):
        """Set data to blank and redraw"""
        self.userlist = []
        self.channel_icon = None
        self.channel_icon_url = None
        self.channel_title = None
        self.connection_status = "DISCONNECTED"
        self.set_needs_redraw()

    def set_fade_out_inactive(self, enabled, fade_time, fade_duration, fade_to):
        """Config option: fade out options"""
        if (self.fade_out_inactive != enabled or self.inactive_time != fade_time or
                self.inactive_fade_time != fade_duration or self.fade_out_limit != fade_to):
            self.fade_out_inactive = enabled
            self.inactive_time = fade_time
            self.inactive_fade_time = fade_duration
            self.fade_out_limit = fade_to
            self.reset_action_timer()

    def set_title_font(self, font):
        """Config option: font used to render title"""
        if self.title_font != font:
            self.title_font = font
            self.set_needs_redraw()

    def set_show_connection(self, show_connection):
        """Config option: show connection status alongside users"""
        if self.show_connection != show_connection:
            self.show_connection = show_connection
            self.set_needs_redraw()

    def set_show_avatar(self, show_avatar):
        """Config option: show avatar icons"""
        if self.show_avatar != show_avatar:
            self.show_avatar = show_avatar
            self.set_needs_redraw()

    def set_show_title(self, show_title):
        """Config option: show channel title alongside users"""
        if self.show_title != show_title:
            self.show_title = show_title
            self.set_needs_redraw()

    def set_show_disconnected(self, show_disconnected):
        """Config option: show even when disconnected from voice chat"""
        if self.show_disconnected != show_disconnected:
            self.show_disconnected = show_disconnected
            self.set_needs_redraw()

    def set_rounded_names(self, rnames):
        """Config option: Draw rounded text backgrounds"""
        if self.rounded_names != rnames:
            self.rounded_names = rnames
            self.set_needs_redraw()

    def set_separate_names(self, separate):
        """Config option: separate name backgrounds from avatar body"""
        if self.separate_names != separate:
            self.separate_names = separate
            self.set_needs_redraw()

    def draw_rounded_rect(self, context, x, y, width, height, radius=6):
        """Helper to draw rounded square/squircle-ish rectangle"""
        radius = max(0, min(radius, width / 2.0, height / 2.0))
        if radius == 0:
            context.new_path()
            context.rectangle(x, y, width, height)
            return

        smoothness = 0.24
        control = radius * smoothness

        context.new_path()
        context.move_to(x + radius, y)
        context.line_to(x + width - radius, y)
        context.curve_to(
            x + width - radius + control, y,
            x + width, y + radius - control,
            x + width, y + radius
        )
        context.line_to(x + width, y + height - radius)
        context.curve_to(
            x + width, y + height - radius + control,
            x + width - radius + control, y + height,
            x + width - radius, y + height
        )
        context.line_to(x + radius, y + height)
        context.curve_to(
            x + radius - control, y + height,
            x, y + height - radius + control,
            x, y + height - radius
        )
        context.line_to(x, y + radius)
        context.curve_to(
            x, y + radius - control,
            x + radius - control, y,
            x + radius, y
        )
        context.close_path()

    def set_show_dummy(self, show_dummy):
        """Config option: Show placeholder information"""
        if self.use_dummy != show_dummy:
            self.use_dummy = show_dummy
            self.set_needs_redraw()

    def set_dummy_count(self, dummy_count):
        """Config option: Change the count of placeholders"""
        if self.dummy_count != dummy_count:
            self.dummy_count = dummy_count
            self.set_needs_redraw()

    def set_overflow_style(self, overflow):
        """Config option: Change handling of too many users to render"""
        if self.overflow != overflow:
            self.overflow = overflow
            self.set_needs_redraw()

    def set_bg(self, background_colour):
        """Config option: Set background colour. Used to draw the transparent window.
         Should not be changed as then the entire screen is obscured"""
        if self.norm_col != background_colour:
            self.norm_col = background_colour
            self.set_needs_redraw()

    def set_fg(self, foreground_colour):
        """Config option: Set foreground colour. Used to render text"""
        if self.text_col != foreground_colour:
            self.text_col = foreground_colour
            self.set_needs_redraw()

    def set_tk(self, talking_colour):
        """Config option: Set talking border colour.
         Used to render border around users who are talking"""
        if self.talk_col != talking_colour:
            self.talk_col = talking_colour
            self.set_needs_redraw()

    def set_mt(self, mute_colour):
        """Config option: Set mute colour. Used to render mute and deaf images"""
        if self.mute_col != mute_colour:
            self.mute_col = mute_colour
            self.set_needs_redraw()

    def set_mute_bg(self, mute_bg_col):
        """Config option: Set mute background colour.
         Used to tint the user avatar before rendering the mute or deaf image above it"""
        if self.mute_bg_col != mute_bg_col:
            self.mute_bg_col = mute_bg_col
            self.set_needs_redraw()

    def set_avatar_bg_col(self, avatar_bg_col):
        """Config option: Set avatar background colour.
         Drawn before user avatar but only visible if default fallback avatar can't be found"""
        if self.avatar_bg_col != avatar_bg_col:
            self.avatar_bg_col = avatar_bg_col
            self.set_needs_redraw()

    def set_hi(self, highlight_colour):
        """Config option: Set talking background colour.
         Used to render the background behind users name."""
        if self.hili_col != highlight_colour:
            self.hili_col = highlight_colour
            self.set_needs_redraw()

    def set_fg_hi(self, highlight_colour):
        """Config option: Set talking text colour.
         Used to render the usernames of users who are talking"""
        if self.text_hili_col != highlight_colour:
            self.text_hili_col = highlight_colour
            self.set_needs_redraw()

    def set_bo(self, border_colour):
        """Config option: Set border colour. Used to render border around users"""
        if self.border_col != border_colour:
            self.border_col = border_colour
            self.set_needs_redraw()

    def set_avatar_size(self, size):
        """Config option: Set avatar size in window-space pixels"""
        if self.avatar_size != size:
            self.avatar_size = size
            self.set_needs_redraw()

    def set_nick_length(self, size):
        """Config option: Limit username length"""
        if self.nick_length != size:
            self.nick_length = size
            self.set_needs_redraw()

    def set_icon_spacing(self, i):
        """Config option: Space between users in the list, in window-space pixels"""
        if self.icon_spacing != i:
            self.icon_spacing = i
            self.set_needs_redraw()

    def set_text_padding(self, i):
        """Config option: Space between user avatar and username, in window-space pixels"""
        if self.text_pad != i:
            self.text_pad = i
            self.set_needs_redraw()

    def set_text_baseline_adj(self, i):
        """Config option: Vertical offset used to render all text, in window-space pixels"""
        if self.text_baseline_adj != i:
            self.text_baseline_adj = i
            self.set_needs_redraw()

    def set_vert_edge_padding(self, i):
        """Config option: Vertical offset from edge of window, in window-space pixels"""
        if self.vert_edge_padding != i:
            self.vert_edge_padding = i
            self.set_needs_redraw()

    def set_horz_edge_padding(self, i):
        """Config option: Horizontal offset from edge of window, in window-space pixels"""
        if self.horz_edge_padding != i:
            self.horz_edge_padding = i
            self.set_needs_redraw()

    def set_square_avatar(self, i):
        """Config option: Mask avatar with a circle before rendering"""
        if self.round_avatar == i:
            self.round_avatar = not i
            self.set_needs_redraw()

    def set_fancy_border(self, border):
        """Config option: Use transparent edges of image as border,
         instead of mask (square/circle)"""
        if self.fancy_border != border:
            self.fancy_border = border
            self.set_needs_redraw()

    def set_only_speaking(self, only_speaking):
        """Config option: Filter user list to only those who
         are talking and those who have stopped talking recently"""
        if self.only_speaking != only_speaking:
            self.only_speaking = only_speaking
            self.set_needs_redraw()

    def set_only_speaking_grace_period(self, grace_period):
        """Config option: How long after stopping speaking the user remains shown"""
        self.only_speaking_grace_period = grace_period
        self.timer_after_draw = grace_period

    def set_highlight_self(self, highlight_self):
        """Config option: Local User should be kept at top of list"""
        if self.highlight_self != highlight_self:
            self.highlight_self = highlight_self
            self.set_needs_redraw()

    def set_order(self, i):
        """Config option: Set method used to order user list"""
        if self.order != i:
            self.order = i
            self.sort_list(self.userlist)
            self.set_needs_redraw()

    def set_icon_only(self, i):
        """Config option: Show only the avatar, without text or its background"""
        if self.icon_only != i:
            self.icon_only = i
            self.set_needs_redraw()

    def set_drawn_border_width(self, width):
        """Config option: Set width of border around username and avatar"""
        if self.border_width != width:
            self.border_width = width
            self.set_needs_redraw()

    def set_horizontal(self, horizontal=False):
        """Config option: Userlist should be drawn horizontally"""
        if self.horizontal != horizontal:
            self.horizontal = horizontal
            self.set_needs_redraw()

    def set_wind_col(self):
        """Use window colour to draw"""
        self.col(self.wind_col, None)

    def set_norm_col(self):
        """Use background colour to draw"""
        self.col(self.norm_col)

    def set_talk_col(self, alpha=1.0):
        """Use talking colour to draw"""
        self.col(self.talk_col, alpha)

    def set_mute_col(self):
        """Use mute colour to draw"""
        self.col(self.mute_col)

    def set_channel_title(self, channel_title):
        """Set title above voice list"""
        if self.channel_title != channel_title:
            self.channel_title = channel_title
            self.set_needs_redraw()

    def set_channel_icon(self, url):
        """Change the icon for channel"""
        if not url:
            self.channel_icon = None
            self.channel_icon_url = None
        else:
            get_surface(self.recv_avatar, url, "channel",
                        self.avatar_size)
            self.channel_icon_url = url

    def set_user_list(self, userlist, alt):
        """Set the users in list to draw"""
        self.userlist = userlist
        for user in userlist:
            if "nick" in user:
                user["friendlyname"] = user["nick"]
            else:
                user["friendlyname"] = user["username"]
        self.sort_list(self.userlist)
        if alt:
            self.reset_action_timer()
            self.set_needs_redraw()

    def set_connection_status(self, connection):
        """Set if discord has a clean connection to server"""
        if self.connection_status != connection['state']:
            self.connection_status = connection['state']
            self.set_needs_redraw()

    def sort_list(self, in_list):
        """Take a userlist and sort it according to config option"""
        if self.order == 1:  # ID Sort
            in_list.sort(key=lambda x: x["id"])
        elif self.order == 2:  # Spoken sort
            in_list.sort(key=lambda x: x["lastspoken"], reverse=True)
            in_list.sort(key=lambda x: x["speaking"], reverse=True)
        else:  # Name sort
            in_list.sort(key=lambda x: locale.strxfrm(x['friendlyname']))
        return in_list

    def has_content(self):
        """Returns true if overlay has meaningful content to render"""
        if not self.enabled:
            return False
        if self.hidden:
            return False
        if self.use_dummy:
            return True
        return self.userlist

    def overlay_draw(self, w, context, data=None):
        """Draw the Overlay"""
        self.context = context
        context.set_antialias(cairo.ANTIALIAS_GOOD)
        # Get size of window
        (width, height) = self.get_size()

        # Make background transparent
        self.set_wind_col()
        # Don't layer drawing over each other, always replace
        context.set_operator(cairo.OPERATOR_SOURCE)
        context.paint()
        context.save()
        if self.piggyback:
            self.piggyback.overlay_draw(w, context, data)
        (floating_x, floating_y, floating_width,
         floating_height) = self.get_floating_coords()
        if self.is_wayland or self.piggyback_parent or self.discover.steamos:
            # Special case! Full-screen window; we clip to floating rect when floating.
            if self.floating:
                context.new_path()
                context.translate(floating_x, floating_y)
                context.rectangle(0, 0, floating_width, floating_height)
                context.clip()
                layout_width = floating_width
                layout_height = floating_height
            else:
                edge_margin = 4
                context.translate(edge_margin, edge_margin)
                layout_width = max(1, width - 2 * edge_margin)
                layout_height = max(1, height - 2 * edge_margin)
        else:
            layout_width = width
            layout_height = height

        context.set_operator(cairo.OPERATOR_OVER)
        if (not self.show_disconnected and self.connection_status == "DISCONNECTED"
                and not self.use_dummy):
            return

        connection = self.discover.connection
        if not connection:
            return
        self_user = connection.user

        # Gather which users to draw
        users_to_draw = self.userlist[:]
        userlist = self.userlist
        if self.use_dummy:  # Sorting every frame is an awful idea. Maybe put this off elsewhere?
            users_to_draw = self.sort_list(self.dummy_data[0:self.dummy_count])
            userlist = self.dummy_data
        now = perf_counter()

        for user in userlist:
            # Bad object equality here, so we need to reassign
            if "id" in self_user and user["id"] == self_user["id"]:
                self_user = user

            # Update friendly name with nick if possible
            if "nick" in user:
                user["friendlyname"] = user["nick"]
            else:
                user["friendlyname"] = user["username"]

            # Remove users that haven't spoken within the grace period
            if self.only_speaking:
                speaking = "speaking" in user and user["speaking"]

                # Extend timer if mid-speaking
                if self.highlight_self and self_user == user:
                    continue
                if speaking:
                    user['lastspoken'] = perf_counter()
                else:
                    grace = self.only_speaking_grace_period

                    if (
                        grace > 0
                        and (last_spoke := user['lastspoken'])
                        and (now - last_spoke) < grace
                    ):
                        # The user spoke within the grace period, so don't hide
                        # them just yet
                        pass

                    elif user in users_to_draw:
                        users_to_draw.remove(user)

        if self.highlight_self:
            if self_user in users_to_draw:
                users_to_draw.remove(self_user)
                users_to_draw.insert(0, self_user)

        avatar_size = self.avatar_size if self.show_avatar else 0
        slot_size = avatar_size
        line_height = slot_size
        avatars_per_row = sys.maxsize

        # Calculate height needed to show overlay
        do_title = False
        do_connection = False
        if self.show_connection:
            users_to_draw.insert(0, None)
            do_connection = True
        if self.show_title and self.channel_title:
            users_to_draw.insert(0, None)
            do_title = True

        if self.horizontal:
            needed_width = (len(users_to_draw) * line_height) + \
                (len(users_to_draw) + 1) * self.icon_spacing

            if needed_width > layout_width:
                if self.overflow == 1:  # Wrap
                    avatars_per_row = int(
                        layout_width / (slot_size + self.icon_spacing))
                elif self.overflow == 2:  # Shrink
                    available_size = layout_width / len(users_to_draw)
                    # Correct math: available_size is the total space including the gap
                    slot_size = available_size - self.icon_spacing
                    avatar_size = slot_size
                    if avatar_size < 8:
                        avatar_size = 8
                        slot_size = avatar_size
                    line_height = slot_size

            current_y = 0 + self.vert_edge_padding
            offset_y = slot_size + self.icon_spacing
            if self.align_right:  # A lie. Align bottom
                current_y = (layout_height - slot_size) - self.vert_edge_padding
                offset_y = -(slot_size + self.icon_spacing)
            rows_to_draw = []
            while len(users_to_draw) > 0:
                row = []
                for _i in range(0, min(avatars_per_row, len(users_to_draw))):
                    row.append(users_to_draw.pop(0))
                rows_to_draw.append(row)
            for row in rows_to_draw:
                needed_width = (len(row) * (line_height + self.icon_spacing))
                current_x = 0 + self.horz_edge_padding
                if self.align_vert == 1:
                    current_x = (layout_width / 2) - (needed_width) / 2
                elif self.align_vert == 2:
                    current_x = layout_width - needed_width - self.horz_edge_padding

                for user in row:
                    if not user:
                        if do_title:
                            do_title = False
                            text_width = self.draw_title(
                                context, current_x, current_y, avatar_size, line_height)
                        elif do_connection:
                            text_width = self.draw_connection(
                                context, current_x, current_y, avatar_size, line_height)
                            do_connection = False
                    else:
                        self.draw_avatar(context, user, current_x,
                                         current_y, avatar_size, line_height)
                    current_x += slot_size + self.icon_spacing
                current_y += offset_y
        else:
            needed_height = ((len(users_to_draw)+0) * line_height) + \
                (len(users_to_draw) + 1) * self.icon_spacing

            if needed_height > layout_height:
                if self.overflow == 1:  # Wrap
                    avatars_per_row = int(
                        layout_height / (slot_size + self.icon_spacing))
                elif self.overflow == 2:  # Shrink
                    available_size = layout_height / len(users_to_draw)
                    # Correct math: available_size is the total space including the gap
                    slot_size = available_size - self.icon_spacing
                    avatar_size = slot_size
                    if avatar_size < 8:
                        avatar_size = 8
                        slot_size = avatar_size
                    line_height = slot_size

            current_x = 0 + self.horz_edge_padding
            offset_x_mult = 1
            offset_x = slot_size + self.horz_edge_padding
            if self.align_right:
                offset_x_mult = -1
                current_x = layout_width - slot_size - self.horz_edge_padding

            # Choose where to start drawing
            current_y = 0 + self.vert_edge_padding
            if self.align_vert == 1:
                current_y = (layout_height / 2) - (needed_height / 2)
            elif self.align_vert == 2:
                current_y = layout_height - needed_height - self.vert_edge_padding

            cols_to_draw = []
            while len(users_to_draw) > 0:
                col = []
                for _i in range(0, min(avatars_per_row, len(users_to_draw))):
                    col.append(users_to_draw.pop(0))
                cols_to_draw.append(col)
            for col in cols_to_draw:
                needed_height = (len(col) * (line_height + self.icon_spacing))
                current_y = 0 + self.vert_edge_padding
                if self.align_vert == 1:
                    current_y = (layout_height / 2) - (needed_height / 2)
                elif self.align_vert == 2:
                    current_y = layout_height - needed_height - self.vert_edge_padding
                largest_text_width = 0
                for user in col:
                    if not user:
                        if do_title:
                            # Draw header
                            text_width = self.draw_title(
                                context, current_x, current_y, avatar_size, line_height)
                            largest_text_width = max(
                                text_width, largest_text_width)
                            current_y += line_height + self.icon_spacing
                            do_title = False
                        elif do_connection:
                            # Draw header
                            text_width = self.draw_connection(
                                context, current_x, current_y, avatar_size, line_height)
                            largest_text_width = max(
                                text_width, largest_text_width)
                            current_y += line_height + self.icon_spacing
                            do_connection = False

                    else:
                        text_width = self.draw_avatar(
                            context, user, current_x, current_y, avatar_size, line_height)
                        largest_text_width = max(
                            text_width, largest_text_width)
                        current_y += line_height + self.icon_spacing
                if largest_text_width > 0:
                    largest_text_width += self.text_pad
                else:
                    largest_text_width = self.icon_spacing
                current_x += offset_x_mult * (offset_x + largest_text_width)

        context.restore()
        self.context = None

    def recv_avatar(self, identifier, pix, mask):
        """Called when image_getter has downloaded an image"""
        if identifier == 'def':
            self.def_avatar = pix
            self.def_avatar_mask = mask
        elif identifier == 'channel':
            self.channel_icon = pix
            self.channel_mask = mask
        else:
            self.avatars[identifier] = pix
            self.avatar_masks[identifier] = mask
        self.set_needs_redraw()

    def delete_avatar(self, identifier):
        """Remove avatar image"""
        if identifier in self.avatars:
            del self.avatars[identifier]

    def draw_title(self, context, pos_x, pos_y, avatar_size, line_height):
        """Draw title at given Y position. Includes both text and image based on settings"""
        av_x = pos_x + (line_height - avatar_size) / 2.0
        av_y = pos_y + (line_height - avatar_size) / 2.0
        tw = 0
        if not self.horizontal and not self.icon_only:
            title = self.channel_title
            if self.use_dummy:
                title = "Dummy Title"
            tw = self.draw_text(
                context, title,
                av_x,
                av_y,
                self.text_col,
                self.norm_col,
                avatar_size,
                line_height,
                self.title_font
            )
        if self.channel_icon:
            self.draw_avatar_pix(context, self.channel_icon, self.channel_mask,
                                 av_x, av_y, None, avatar_size)
        else:
            self.blank_avatar(context, av_x, av_y, avatar_size)
            if self.channel_icon_url:
                get_surface(self.recv_avatar, self.channel_icon_url, "channel",
                            self.avatar_size)
        return tw

    def unused_fn_needed_translations(self):
        """
        These are here to force them to be picked up for translations

        They're fed right through from Discord client as string literals
        """
        _("DISCONNECTED")
        _("NO_ROUTE")
        _("VOICE_DISCONNECTED")
        _("ICE_CHECKING")
        _("AWAITING_ENDPOINT")
        _("AUTHENTICATING")
        _("CONNECTING")
        _("CONNECTED")
        _("VOICE_CONNECTING")
        _("VOICE_CONNECTED")

    def draw_connection(self, context, pos_x, pos_y, avatar_size, line_height):
        """Draw title at given Y position. Includes both text and image based on settings"""
        av_x = pos_x + (line_height - avatar_size) / 2.0
        av_y = pos_y + (line_height - avatar_size) / 2.0
        tw = 0
        if not self.horizontal and not self.icon_only:
            tw = self.draw_text(
                context, _(self.connection_status),
                av_x,
                av_y,
                self.text_col,
                self.norm_col,
                avatar_size,
                line_height,
                self.text_font
            )
        self.blank_avatar(context, av_x, av_y, avatar_size)
        self.draw_connection_icon(context, av_x, av_y, avatar_size)
        return tw

    def draw_avatar(self, context, user, pos_x, pos_y, avatar_size, line_height):
        """Draw avatar at given Y position. Includes both text and image based on settings"""
        av_x = pos_x + (line_height - avatar_size) / 2.0
        av_y = pos_y + (line_height - avatar_size) / 2.0
        # Ensure pixbuf for avatar
        if user["id"] not in self.avatars and user["avatar"] and avatar_size > 0:
            url = f"https://cdn.discordapp.com/avatars/{user['id']}/{user['avatar']}.png"
            get_surface(self.recv_avatar, url, user["id"],
                        self.avatar_size)

            # Set the key with no value to avoid spamming requests
            self.avatars[user["id"]] = None
            self.avatar_masks[user["id"]] = None

        colour = None
        mute = False
        deaf = False
        bg_col = None
        fg_col = None
        tw = 0

        if "mute" in user and user["mute"]:
            mute = True
        if "deaf" in user and user["deaf"]:
            deaf = True
        if "speaking" in user and user["speaking"] and not deaf and not mute:
            colour = self.talk_col
        if "speaking" in user and user["speaking"] and not deaf and not mute:
            bg_col = self.hili_col
            fg_col = self.text_hili_col
        else:
            bg_col = self.norm_col
            fg_col = self.text_col

        pix = None
        mask = None
        if user["id"] in self.avatars:
            pix = self.avatars[user["id"]]
            mask = self.avatar_masks[user["id"]]
        if not self.horizontal:
            if not self.icon_only:
                tw = self.draw_text(
                    context, user["friendlyname"],
                    av_x,
                    av_y,
                    fg_col,
                    bg_col,
                    avatar_size,
                    line_height,
                    self.text_font
                )
        self.draw_avatar_pix(context, pix, mask, av_x,
                             av_y, colour, avatar_size)
        if deaf:
            self.draw_deaf(context, av_x, av_y,
                           self.mute_bg_col, avatar_size)
        elif mute:
            self.draw_mute(context, av_x, av_y,
                           self.mute_bg_col, avatar_size)
        return tw

    def draw_text(self, context, string, pos_x, pos_y,
                  tx_col, bg_col, avatar_size, line_height, font):
        """Draw username & background at given position"""
        if self.nick_length < 32 and len(string) > self.nick_length:
            string = string[:(self.nick_length-1)] + "\u2026"

        context.save()
        layout = self.create_pango_layout(string)
        layout.set_auto_dir(True)
        layout.set_markup(string, -1)
        (_floating_x, _floating_y, floating_width,
         _floating_height) = self.get_floating_coords()
        layout.set_width(Pango.SCALE * floating_width)
        layout.set_spacing(Pango.SCALE * 3)
        if font:
            font = Pango.FontDescription(font)
            layout.set_font_description(font)
        (ink_rect, logical_rect) = layout.get_pixel_extents()
        text_height = logical_rect.height
        text_width = logical_rect.width
        layout.set_width(Pango.SCALE * text_width)

        self.col(tx_col)
        height_offset = (line_height / 2) - (text_height / 2)
        text_y_offset = height_offset + self.text_baseline_adj

        reported_width = text_width
        separate_pill = self.show_avatar and self.separate_names
        name_gap = 0
        if separate_pill:
            name_gap = max(8, int(round(self.text_pad)))
            pill_pad_x = 8
            pill_pad_y = 3
            bg_height = text_height + (pill_pad_y * 2)
            bg_width = text_width + (pill_pad_x * 2)
            rounded_radius = min(6, max(3, bg_height * 0.25))
            reported_width = bg_width + name_gap
        else:
            bg_height = text_height + 12
            bg_width = text_width + (self.text_pad * 4)
            rounded_radius = min(7, max(3, bg_height * 0.22))
            rounded_radius = min(rounded_radius, bg_width * 0.14)

        if self.align_right:
            context.move_to(0, 0)
            self.col(bg_col)
            if separate_pill:
                bg_x = pos_x - name_gap - bg_width
            else:
                bg_x = pos_x - text_width - (self.text_pad * 2)
            bg_y = pos_y + (line_height / 2) - (bg_height / 2)
            if self.is_wayland:
                context.save()
                context.set_antialias(cairo.ANTIALIAS_NONE)
            if self.rounded_names:
                if self.is_wayland:
                    # Avoid corner fringe leaking desktop through anti-aliased edges.
                    bg_x -= 1.0
                    bg_y -= 1.0
                    bg_width += 2.0
                    bg_height += 2.0
                self.draw_rounded_rect(context,
                    bg_x,
                    bg_y,
                    bg_width,
                    bg_height,
                    radius=rounded_radius)
                context.fill()
            else:
                context.rectangle(
                    bg_x,
                    bg_y,
                    bg_width,
                    bg_height
                )
                context.fill()
            if self.is_wayland:
                context.restore()

            self.col(tx_col)
            if separate_pill:
                text_x = bg_x + pill_pad_x
                text_y = pos_y + (line_height / 2) - (text_height / 2) + self.text_baseline_adj
                context.move_to(text_x - ink_rect.x, text_y)
            else:
                context.move_to(
                    pos_x - text_width - self.text_pad - ink_rect.x,
                    pos_y + text_y_offset)
            layout.set_alignment(Pango.Alignment.RIGHT)
            PangoCairo.show_layout(context, layout)
        else:
            context.move_to(0, 0)
            self.col(bg_col)
            if separate_pill:
                bg_x = pos_x + avatar_size + name_gap
            else:
                bg_x = pos_x - (self.text_pad * 2) + avatar_size
            bg_y = pos_y + (line_height / 2) - (bg_height / 2)
            if self.is_wayland:
                context.save()
                context.set_antialias(cairo.ANTIALIAS_NONE)
            if self.rounded_names:
                if self.is_wayland:
                    # Avoid corner fringe leaking desktop through anti-aliased edges.
                    bg_x -= 1.0
                    bg_y -= 1.0
                    bg_width += 2.0
                    bg_height += 2.0
                self.draw_rounded_rect(context,
                    bg_x,
                    bg_y,
                    bg_width,
                    bg_height,
                    radius=rounded_radius)
                context.fill()
            else:
                context.rectangle(
                    bg_x,
                    bg_y,
                    bg_width,
                    bg_height
                )
                context.fill()
            if self.is_wayland:
                context.restore()

            self.col(tx_col)
            if separate_pill:
                text_x = bg_x + pill_pad_x
                text_y = pos_y + (line_height / 2) - (text_height / 2) + self.text_baseline_adj
                context.move_to(text_x - ink_rect.x, text_y)
            else:
                context.move_to(
                    pos_x + self.text_pad + avatar_size - ink_rect.x,
                    pos_y + text_y_offset)
            layout.set_alignment(Pango.Alignment.LEFT)
            PangoCairo.show_layout(context, layout)
        context.restore()
        return reported_width

    def _circle_path(self, context, cx, cy, radius, overshoot=0):
        """Draw a full circle path, optionally overshooting for anti-aliased edge coverage."""
        r = max(0.001, radius + overshoot)
        context.arc(cx, cy, r, 0, 2 * math.pi)

    def blank_avatar(self, context, pos_x, pos_y, avatar_size):
        """Draw a cut-out of the previous shape with a forcible transparent hole"""
        context.save()
        if self.round_avatar:
            context.new_path()
            cx = pos_x + (avatar_size / 2)
            cy = pos_y + (avatar_size / 2)
            self._circle_path(context, cx, cy, avatar_size / 2)
            context.clip()
        self.col(self.avatar_bg_col)
        context.set_operator(cairo.OPERATOR_SOURCE)
        context.rectangle(pos_x, pos_y, avatar_size, avatar_size)
        context.fill()
        context.restore()

    def draw_avatar_pix(self, context, pixbuf, mask, pos_x, pos_y, border_colour, avatar_size):
        """Draw avatar image at given position"""
        if not self.show_avatar:
            return
        # Empty the space for this
        self.blank_avatar(context, pos_x, pos_y, avatar_size)

        # fallback default or fallback further to no image here
        if not pixbuf:
            pixbuf = self.def_avatar
            if not pixbuf:
                return
        if not mask:
            mask = self.def_avatar_mask
            if not mask:
                return

        # Draw the image
        context.save()
        if self.round_avatar:
            context.new_path()
            cx = pos_x + (avatar_size / 2)
            cy = pos_y + (avatar_size / 2)
            self._circle_path(context, cx, cy, avatar_size / 2)
            context.clip()
        context.set_operator(cairo.OPERATOR_OVER)
        draw_img_to_rect(pixbuf, context, pos_x, pos_y,
                         avatar_size, avatar_size, False, False, 0, 0,
                         self.fade_opacity * self.icon_transparency)
        context.restore()

        # Draw the "border" on top
        if border_colour:
            self.col(border_colour)
            if self.round_avatar:
                context.new_path()
                cx = pos_x + (avatar_size / 2)
                cy = pos_y + (avatar_size / 2)
                # Radius is inset by half the border width so the stroke is entirely inside the avatar
                self._circle_path(context, cx, cy,
                                  (avatar_size / 2.0) - (self.border_width / 2.0),
                                  overshoot=0)
                context.set_line_width(self.border_width)
                context.stroke()
            else:
                context.new_path()
                context.rectangle(pos_x + (self.border_width / 2.0),
                                  pos_y + (self.border_width / 2.0),
                                  avatar_size - self.border_width,
                                  avatar_size - self.border_width)
                context.set_line_width(self.border_width)
                context.stroke()

    def draw_mute(self, context, pos_x, pos_y, bg_col, avatar_size):
        """Draw Mute logo"""
        if avatar_size <= 0:
            return
        context.save()
        
        icon_size = max(avatar_size * 0.5, 12)
        offset_x = pos_x + avatar_size - (icon_size * 0.9)
        offset_y = pos_y + avatar_size - (icon_size * 0.9)

        context.translate(offset_x, offset_y)
        context.scale(icon_size, icon_size)

        # Add a dark bubble background
        context.save()
        context.set_operator(cairo.OPERATOR_OVER)
        context.arc(0.5, 0.5, 0.5, 0, 2 * math.pi)
        context.clip()
        self.col([0.0, 0.0, 0.0, 0.8])
        context.rectangle(0, 0, 1, 1)
        context.fill()
        context.restore()

        # Red tint overlay icon color
        self.col([1.0, 0.3, 0.3, 1.0])
        context.save()

        # Clip Strike-through
        context.new_path()
        context.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
        context.set_line_width(0.1)
        context.move_to(0.0, 0.0)
        context.line_to(1.0, 0.0)
        context.line_to(1.0, 1.0)
        context.line_to(0.0, 1.0)
        context.line_to(0.0, 0.0)
        context.close_path()
        context.new_sub_path()
        context.arc(0.9, 0.1, 0.05, 1.25 * math.pi, 2.25 * math.pi)
        context.arc(0.1, 0.9, 0.05, .25 * math.pi, 1.25 * math.pi)
        context.close_path()
        context.clip()

        # Center
        context.set_line_width(0.07)
        context.arc(0.5, 0.3, 0.1, math.pi, 2 * math.pi)
        context.arc(0.5, 0.5, 0.1, 0, math.pi)
        context.close_path()
        context.fill()

        context.set_line_width(0.05)

        # Stand rounded
        context.arc(0.5, 0.5, 0.15, 0, 1.0 * math.pi)
        context.stroke()

        # Stand vertical
        context.move_to(0.5, 0.65)
        context.line_to(0.5, 0.75)
        context.stroke()

        # Stand horizontal
        context.move_to(0.35, 0.75)
        context.line_to(0.65, 0.75)
        context.stroke()

        context.restore()
        # Strike through
        context.arc(0.7, 0.3, 0.035, 1.25 * math.pi, 2.25 * math.pi)
        context.arc(0.3, 0.7, 0.035, .25 * math.pi, 1.25 * math.pi)
        context.close_path()
        context.fill()
        context.set_fill_rule(cairo.FILL_RULE_WINDING)

        context.restore()

    def draw_deaf(self, context, pos_x, pos_y, bg_col, avatar_size):
        """Draw deaf logo"""
        if avatar_size <= 0:
            return
        context.save()

        icon_size = max(avatar_size * 0.5, 12)
        offset_x = pos_x + avatar_size - (icon_size * 0.9)
        offset_y = pos_y + avatar_size - (icon_size * 0.9)

        context.translate(offset_x, offset_y)
        context.scale(icon_size, icon_size)

        # Add a dark bubble background
        context.save()
        context.set_operator(cairo.OPERATOR_OVER)
        context.arc(0.5, 0.5, 0.5, 0, 2 * math.pi)
        context.clip()
        self.col([0.0, 0.0, 0.0, 0.8])
        context.rectangle(0, 0, 1, 1)
        context.fill()
        context.restore()

        # Red tint overlay icon color
        self.col([1.0, 0.3, 0.3, 1.0])
        context.save()

        # Clip Strike-through
        context.new_path()
        context.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
        context.set_line_width(0.1)
        context.move_to(0.0, 0.0)
        context.line_to(1.0, 0.0)
        context.line_to(1.0, 1.0)
        context.line_to(0.0, 1.0)
        context.line_to(0.0, 0.0)
        context.close_path()
        context.new_sub_path()
        context.arc(0.9, 0.1, 0.05, 1.25 * math.pi, 2.25 * math.pi)
        context.arc(0.1, 0.9, 0.05, .25 * math.pi, 1.25 * math.pi)
        context.close_path()
        context.clip()

        # Top band
        context.arc(0.5, 0.5, 0.2, 1.0 * math.pi, 0)
        context.stroke()

        # Left band
        context.arc(0.28, 0.65, 0.075, 1.5 * math.pi, 0.5 * math.pi)
        context.move_to(0.3, 0.5)
        context.line_to(0.3, 0.75)
        context.stroke()

        # Right band
        context.arc(0.72, 0.65, 0.075, 0.5 * math.pi, 1.5 * math.pi)
        context.move_to(0.7, 0.5)
        context.line_to(0.7, 0.75)
        context.stroke()

        context.restore()
        # Strike through
        context.arc(0.7, 0.3, 0.035, 1.25 * math.pi, 2.25 * math.pi)
        context.arc(0.3, 0.7, 0.035, .25 * math.pi, 1.25 * math.pi)
        context.close_path()
        context.fill()
        context.set_fill_rule(cairo.FILL_RULE_WINDING)

        context.restore()

    def draw_connection_icon(self, context, pos_x, pos_y, avatar_size):
        """Draw a series of bars to show connectivity state"""
        context.save()
        context.translate(pos_x, pos_y)
        context.scale(avatar_size, avatar_size)

        bars = 0
        s = self.connection_status
        if s == "DISCONNECTED" or s == "NO_ROUTE" or s == "VOICE_DISCONNECTED":
            bars = 0
            self.col([1.0, 0.0, 0.0, 1.0])
        elif s == "ICE_CHECKING" or s == "AWAITING_ENDPOINT" or s == "AUTHENTICATING":
            bars = 1
            self.col([1.0, 0.0, 0.0, 1.0])
        elif s == "CONNECTING" or s == "CONNECTED" or s == "VOICE_CONNECTING":
            bars = 2
            self.col([1.0, 1.0, 0.0, 1.0])
        elif s == "VOICE_CONNECTED":
            bars = 3
            self.col([0.0, 1.0, 0.0, 1.0])
        context.set_line_width(0.1)

        if bars >= 1:
            context.move_to(0.3, 0.8)
            context.line_to(0.3, 0.6)
            context.stroke()
        if bars >= 2:
            context.move_to(0.5, 0.8)
            context.line_to(0.5, 0.4)
            context.stroke()
        if bars == 3:
            context.move_to(0.7, 0.8)
            context.line_to(0.7, 0.2)
            context.stroke()
        context.restore()
