pkgname=discover-overlay-git
pkgver=0.7.9
pkgrel=1
pkgdesc="Voice chat overlay"
arch=('any')
url="https://github.com/trigg/Discover"
license=('GPL3')
depends=('python' 'python-gobject' 'python-requests' 'python-pillow' 'python-xlib')
makedepends=('python-setuptools')

package() {
  cd "$startdir"
  python setup.py install --root="$pkgdir" --optimize=1
}
