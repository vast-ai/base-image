# Third-Party Licenses

This image bundles the following vendor application(s). Each is the property of
its respective authors and is distributed under the license shown below. Where
the vendor's source or LICENSE file is shipped inside this image at a known
location, the path is given. Otherwise, the upstream repository is referenced
as the canonical source for the license text.

## Selkies-GStreamer

- **License:** MPL-2.0
- **Upstream:** https://github.com/selkies-project/selkies-gstreamer
- **License file in image:** Included in the pip-installed package under
  `/usr/lib/python3/dist-packages/selkies_gstreamer-*.dist-info/LICENSE`

## Apache Guacamole

- **License:** Apache-2.0
- **Upstream:** https://guacamole.apache.org/
- **License file in image:** Compiled from source. The Apache-2.0 license text
  is available at `/usr/share/common-licenses/Apache-2.0` in the Debian/Ubuntu
  base image.

## Blender

- **License:** GPL-2.0-or-later
- **Upstream:** https://www.blender.org/
- **License file in image:** `/opt/blender-*/copyright.txt` (and the full GPL
  text under `/opt/blender-*/`)

## VirtualGL

- **License:** wxWindows Library Licence 3.1 (LGPL-2.1 with an exception for binary distribution)
- **Upstream:** https://github.com/VirtualGL/virtualgl
- **License file in image:** `/usr/share/doc/virtualgl-*/LICENSE.txt` (installed by
  the release `.deb`)

## nvidia-vaapi-driver

- **License:** MIT
- **Upstream:** https://github.com/elFarto/nvidia-vaapi-driver
- **License file in image:** Not shipped - the driver is built from source and the
  source tree is removed. See `COPYING` in the upstream repository.

## guacamole-auth-noauth extension

- **License:** None declared - the upstream repository ships no LICENSE file
- **Upstream:** https://github.com/GauriSpears/guacamole-noauth
- **License file in image:** None
