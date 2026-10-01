# README screenshots

- `studio-imager.png`: Smart Imager hardware/image/drive selection.
- `studio-credentials.png`: first-boot account, network and service settings.

Captured on Windows on 2026-10-01 from the actual Studio Python application and
its native Microsoft Edge WebView2 renderer, using `CapturePreviewAsync`.
The capture used the unchanged frontend at source revision
`84f7082d310224be46c30c1c587eaf425a6bee7b`, with the release metadata prepared for
0.5.0-rc.2. The source backend and JavaScript bridge were active; no browser
fixture, mock device, fake SSH connection or generated success state was used.
An isolated empty preferences file selected the normal dark theme and disabled
automatic network scanning. Account/network password fields were empty.

The screenshots show preparation only. No storage was written and no printer
connection or physical qualification is implied. The user interface is captured
as shipped, including its current mixed-language labels; the README captions
are translated in EN/ES/PT. The existing banner remains `web/KACE-studio-banner.png`.
