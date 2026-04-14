# Third-Party Licenses

This image bundles the following vendor application(s). Each is the property of
its respective authors and is distributed under the license shown below. The
full license text for each application is included with its source inside this
image at the path indicated.

## Open WebUI

- **License:** Open WebUI License (BSD-3-Clause + branding restriction)
- **Upstream:** https://github.com/open-webui/open-webui
- **License file in image:** See the upstream repository LICENSE file. This
  image is built on top of the official `ghcr.io/open-webui/open-webui` Docker
  image; the Open WebUI application is unchanged from upstream.
- **Notes:** Open WebUI uses a custom license based on BSD-3-Clause with an
  additional clause prohibiting removal or alteration of the "Open WebUI"
  branding in deployments serving more than 50 users in a 30-day period,
  unless an enterprise license has been obtained. See the upstream LICENSE
  file for the full terms.

## Ollama

- **License:** MIT
- **Upstream:** https://github.com/ollama/ollama
- **License file in image:** See the upstream repository LICENSE file. The
  Ollama binary is bundled into this image and runs alongside Open WebUI.
