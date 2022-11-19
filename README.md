# Discord Custom RPC
*A modified version of Cataiana's [upstream customrpc project](https://github.com/CataIana/customrpc)*  
  
This Python 3 script connects to a variety of sources and publishes that information as your Discord status.
Supported sources:
- A variety of internet websites through WebNowPlaying
  - Requires the use of [my fork](https://github.com/jacksonrakena/WebNowPlaying-BrowserExtension)
  - YouTube videos, Twitch streams, + a variety of other sites
- Spotify (through RPC), including showing your playlist name and album covers
- Detecting running applications on Windows
  - Requires you to whitelist them in `config.json`

## Requirements
- Python 3
- Install the requirements: `python -m pip install -r requirements.txt`
- Install my modified WebNowPlaying browser extension: [https://github.com/jacksonrakena/WebNowPlaying-BrowserExtension](https://github.com/jacksonrakena/WebNowPlaying-BrowserExtension)
- Copy `exampleconfig.json` to `config.json` and fill out the fields
  - `large_image_urls` must have at least one image URL
- Start `customrpc.pyw`

## Screenshots
Shows Spotify playlist names and other information:  
![](https://media.discordapp.net/attachments/749730606786805792/1043339649789210734/image.png)

Shows the Twitch stream you're watching:  
![](https://media.discordapp.net/attachments/763970291675562007/1043327547322146816/image.png)

Shows the YouTube stream or video you're watching:  
![](https://media.discordapp.net/attachments/800926123851644969/1043341568263860315/image.png)
![](https://media.discordapp.net/attachments/763970291675562007/1043328739313979412/image.png)
