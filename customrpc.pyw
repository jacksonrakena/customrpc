import logging
import signal
import sys
from dataclasses import dataclass
from json import load as j_load
from json.decoder import JSONDecodeError
from os import getcwd
from random import choice
from threading import Thread
from time import localtime, sleep, strftime, time
from traceback import format_tb
from typing import Optional

from psutil import boot_time, process_iter
from pypresence import Presence
from pypresence.exceptions import (DiscordError, DiscordNotFound, InvalidID,
                                   InvalidPipe)
from requests import ConnectTimeout, get
from spotipy import Spotify, SpotifyException, SpotifyOAuth
from xml_to_dict import XMLtoDict

from constants import REFRESH_TIME_SECONDS
from mcache import MemoryCache
from wnp_client import client_init


#This class is just intended to be something that more or less mimics None without it actually being a nonetype
# It's used as a fill in for when a Client ID is not provided for an application
class Empty:
    def __eq__(self, other):
        return False

    def __bool__(self):
        return False

    def __repr__(self):
        return "No RPC"

NoRPC = Empty()

@dataclass
class Button:
    label: str
    url: str

@dataclass
class Payload:
    state: str
    details: str
    small_image: str = None
    small_text: str = None
    large_image: str = None
    large_text: str = None
    start: int = None
    end: int = None
    buttons: list = None

    def to_dict(self):
        return self.__dict__

    def add_button(self, button: Button):
        if self.buttons is None:
            self.buttons = []
        if len(self.buttons) < 2:
            self.buttons.append(button)
        else:
            raise TypeError("Cannot add more than 2 buttons!")

    def __str__(self):
        return str({k:v for k,v in self.__dict__.items() if v})

    def __repr__(self):
        return repr(str(self.__dict__))
    
    # Simple function to compare if 2 numbers are within 3 seconds of them. 
    # Sometimes the start/end times can be a second or two off and we don't want to update because of that
    # I just googled this
    def compare_times(self, a: int, b: int) -> bool:
        try:
            if abs(a-b) < 3:
                return True
        except TypeError:
            return True

    def __eq__(self, other):
        for x, y in self.__dict__.items():
            if x in ["large_image", "small_image"]:  # Things to not bother comparing
                continue
            if x in ["start", "end"]:
                if not self.compare_times(getattr(other, x, None), y):
                    return False
            else:
                if getattr(other, x, None) != y:
                    return False
        return True


class CustomRPC():
    def __init__(self):
        with open(f"{getcwd()}/config.json") as f:
            self.config = j_load(f)

        if __name__ == "__main__":
            signal.signal(signal.SIGINT, self.close)

        #Setup logging
        self.format = logging.Formatter('%(asctime)s:%(levelname)s:%(name)s: %(message)s')
        self.log_level = logging.DEBUG
        self.log = logging.getLogger("customrpc")
        self.log.setLevel(self.log_level)

        # When logging to files only log warnings, log is cleared on every restart
        fhandler = logging.FileHandler(filename="rpc.log", encoding="utf-8", mode="w+")
        fhandler.setLevel(logging.WARNING)
        fhandler.setFormatter(self.format)
        self.log.addHandler(fhandler)

        # Ensure logging is also done to console
        chandler = logging.StreamHandler(sys.stdout)                             
        chandler.setLevel(self.log_level)
        chandler.setFormatter(self.format)                                        
        self.log.addHandler(chandler)

        self.playlists = MemoryCache('playlists')
        self.prev_cid = None # The last client ID used for connecting. Used for comparasions
        self.connected = False # If the RPC is currently connected. This can only really be assumed 
        self.previous_payload: Optional[Payload] = None # Temporary var for comparing between payloads to decide if we need to send an update to discord
        self.force_update = True # If we should be updating the RPC no matter what. Generally used for Client ID switching and reconnections
        self.last_update = 0
        self.auth_spotify() # Authorize spotify, so we can connect an pull data. 
        self.xml_parser = XMLtoDict() # Used for parsing VLC data, since it replies in XML

    def auth_spotify(self):
        self.log.debug("Authorising Spotify")
        auth_manager=SpotifyOAuth(scope="user-read-currently-playing user-read-playback-state", **self.config["spotify"])
        self.sp = Spotify(auth_manager=auth_manager)

    def reconnect(self, client_id=None):
        if client_id is None: # If a client ID hasn't been set, just go with default
            client_id = self.config["default_cid"]
        if self.connected: # Reset connection if necessary
            self.RPC.close()
            self.connected = False
        self.log.info(f"Connecting with Client ID {client_id}")
        while True: # Loop indefinitely until connection is established. Will loop here if discord isn't open
            try:
                self.RPC = Presence(client_id=client_id) # Reinit presence class
                self.RPC.connect()
            except InvalidID as e:
                self.log.debug(f"Retrying... ({e})")
            except InvalidPipe as e:
                self.log.debug(f"Retrying... ({e})")
            except DiscordError as e:
                self.log.debug(f"Retrying... ({e})")
            except DiscordNotFound as e:
                self.log.debug(f"Retrying... ({e})")
            else:
                break
            sleep(2)
        self.log.info("Connected")
        self.force_update = True # Client ID switched, ensure we update no matter what
        self.connected = True # Since we escaped the loop, we must be connected

    # Function to check if the new payload is the same as the old one. 
    # Ignores large/small image changes and allows for small disprepencies with start/end time
    # Returns false if they aren't the same, and true if they are
    def same_payload(self, payload) -> bool:
        if self.previous_payload == None:
            self.previous_payload = payload
        if self.force_update:
            self.force_update = False
            return False
        return payload == self.previous_payload

    def get_payload(self):
        extra_button = None # Set as none to prevent issues
        media_button = None
        # Set payload to fallback information. They will be replaced if necessary
        payload = Payload(details = self.config["fallback_details"],
            state = self.config["fallback_state"],
            large_image = choice(self.config["large_image_urls"]).lower(),
            large_text = self.config["fallback_largetext"])
        if self.config["use_extra_button"]: # If enabled, use the extra button information set in the config
            extra_button = self.config["extra_button"]
        client_id = None 
        if self.config["show_spotify"]: # If spotify is enabled, fetch data for it
            try:
                spotify = self.sp.current_user_playing_track()
            except SpotifyException:
                try:
                    self.auth_spotify()
                except SpotifyException:
                    self.log.error("Unable to connect to spotify!")
                    spotify = None
                else:
                    spotify = self.sp.current_user_playing_track()
            if spotify is None:
                client_id = None
            else:
                if not spotify["is_playing"]: # If a song isn't playing, move on
                    client_id = None
                else:
                    try: # Otherwise, form the data, creating the "Play on spotify" button and displaying the song name - artist
                        # And do some maths to make the start/end time, depending on whichever the config says to use. 
                        # This is an epoch/unix time and we just minus/plus the progress


                        payload.details = f"{spotify['item']['name']} — {spotify['item']['artists'][0]['name']}"
                        if spotify['item']['name'] != spotify['item']['album']['name']:
                            payload.state = f"on {spotify['item']['album']['name']}"
                        else:
                            payload.state = None

                        if spotify['context'] is not None and spotify['context']['type'] == 'playlist':
                            payload.state = spotify['context']['uri']
                            playlist_id = spotify['context']['uri']
                            playlist_name = self.playlists.get(playlist_id)
                            if playlist_name is None:
                                playlist_name = self.sp.playlist(playlist_id)['name']
                                self.playlists.put(playlist_id, playlist_name)
                            payload.state = playlist_name
                        media_button = {"label": "Play on Spotify", "url": spotify["item"]["external_urls"]["spotify"]}
                        client_id = self.config["spotify_cid"] # Set the spotify Client ID
                        if self.config["use_time_left_media"] == True:
                            payload.end = time() + (int(spotify["item"]["duration_ms"]/1000) - int(spotify["progress_ms"]/1000))
                        else:
                            payload.start = int(time() - int(spotify["progress_ms"]/1000))
                        payload.small_image = self.config["spotify_icon"] # Get small image spotify icon
                        payload.large_image = spotify['item']['album']['images'][0]['url'] # Get large image from album art
                        payload.large_text = f"{spotify['item']['album']['name']} — {spotify['item']['artists'][0]['name']}"
                    except KeyError as e: # If something failed, just log it and move on
                        formatted_exception = "Traceback (most recent call last):\n" + ''.join(format_tb(e.__traceback__)) + f"{type(e).__name__}: {e}"
                        self.log.error(formatted_exception)
        # If we want to show VLC or games, we need to fetch the process list, storing the data, along with their config information
        if self.config["show_other_media"] or self.config["show_games"]:
            games = list(self.config["games"].keys())+["vlc.exe"]+["plex.exe"]
            processes = {p.name(): {"object": p, "info": self.config["games"].get(p.name().lower(), None)} for p in process_iter() if p.name().lower() in games}
        if self.config["show_other_media"]:
            process = processes.get("vlc.exe", None) # Check if VLC is in the running processes list.
            if process is not None:
                process_info = process["info"]
                try: # If VLC is running, make a request to it's API to fetch what if currently playing. If it fails, just ignore and move on. It does fail alot
                    r = get("http://localhost:8080/requests/status.xml", verify=False, auth=("", self.config["vlc_http_password"]), timeout=2)
                except ConnectionError as e:
                    self.log.debug(f"Connection error processing VLC dict: {e}")
                except ConnectTimeout as e:
                    self.log.debug(f"Connection error processing VLC dict: {e}")
                else:
                    try:
                        # use the xml parser to parse the mess of response that VLC gives us
                        p = self.xml_parser.parse(r.text)["root"]
                        if p["state"] == "playing": # We only want to set to VLC if something is playing currently
                            vlctitle = None
                            vlcartist = None
                            # Some mess to parse the craze of VLC information, with some fallbacks. Some media files contain data that others don't
                            for x in p["information"]["category"][0]["info"]:
                                if type(p["information"]["category"][0]["info"]) is not list:
                                    x = p["information"]["category"][0]["info"]
                                if x["@name"] == "title":
                                    vlctitle = x["#text"]
                                if x["@name"] == "filename":
                                    vlcfilename = x["#text"]
                                if x["@name"] == "artist":
                                    vlcartist = x["#text"]
                            if vlctitle is None:
                                vlctitle = vlcfilename
                            payload.state = f"{vlctitle}{' - ' if vlcartist else ''}{vlcartist if vlcartist else ''}"[:112] #Ensure that the name doesn't hit the character limit, limiting it to 112 characters
                            if self.config["use_time_left_media"] == True: # Set unix time of start/end time
                                payload.end = int(time() + (int(p["length"]) - int(p["time"])))
                            else:
                                payload.start = int(time() - int(p["time"]))
                            payload.small_image = self.config["vlc_icon"] # And finally set small icon and client ID, since we know everything else worked
                            client_id = self.config["vlc_cid"]
                    except KeyError as e: #In case any weird errors occured fetching data, I'd wanna find out why
                        self.log.debug(f"KeyError processing VLC dict: {e}")

            process = processes.get("Plex.exe", None) # Check if VLC is in the running processes list.
            if process is not None:
                process_info = process["info"]
                try: # If Plex is running, make a request to it's API to fetch what if currently playing. If it fails, just ignore and move on. It does fail alot
                    r = get("http://192.168.86.237:32400/status/sessions/?X-Plex-Token=3vMkbwVXRUGpxS9t6KaU")
                except ConnectionError as e:
                    self.log.debug(f"Connection error processing Plex XML: {e}")
                except ConnectTimeout as e:
                    self.log.debug(f"Connection error processing Plex XML: {e}")
                else:
                    try:
                        # use the xml parser to parse the mess of response that VLC gives us
                        p = self.xml_parser.parse(r.text)["MediaContainer"]["Video"]
                        if p["Player"]["@state"] == "playing": # We only want to set to Plex if something is playing currently
                            print(p["@type"])
                            if p["@type"] == "episode":
                                plextitle = f"{p['@grandparentTitle'][:104]} S{p['@parentIndex']} E{p['@index']}"
                            elif p["@type"] == "movie":
                                plextitle = f"{p['@title']}"[:112]
                            payload.state = f"{plextitle}" #Ensure that the name doesn't hit the character limit, limiting it to 112 characters
                            if self.config["use_time_left_media"] == True: # Set unix time of start/end time
                                payload.end = int(time() + (int(p["@duration"])/1000 - int(p["@viewOffset"])/1000))
                            else:
                                payload.start = int(time() - int(p["@viewOffset"])/1000)
                            payload.small_image = self.config["plex_icon"] # And finally set small icon and client ID, since we know everything else worked
                            client_id = self.config["plex_cid"]
                    except KeyError as e: #In case any weird errors occured fetching data, I'd wanna find out why
                        self.log.debug(f"KeyError processing Plex XML: {e}")

            webnp = {}
            failed = 0
            while webnp == {}:
                try:
                    with open(".info.json") as f:
                        webnp = j_load(f)
                except JSONDecodeError:
                    failed += 1 # Sometimes we read the file right as it is being written to. Retry a few times so we may get valid data. If not, just ignore
                    if failed > 9:
                        break
                except FileNotFoundError:
                    break
            if time() - webnp.get("last_update", 0) < 10: # Sometimes there's old data, ignore it if it hasn't been updated in the last 10 seconds. May occur on process crash/etc
                if webnp.get("state", None) == "1":
                    if webnp["player"] in self.config["other_media"].keys(): # Check if the player type is defined in the config, so we use their custom client ids/etc
                        client_id = self.config["other_media"][webnp["player"]]["client_id"]
                        if len(f"{webnp['title']} - {webnp['artist']}") > 128: # Run some weird maths to cut off the title if it is too long, while ensuring the artist length won't make it too long
                            payload.details = f"{webnp['title'][:-(len(webnp['artist'])-(128-len(webnp['artist'])-3))]}..."
                        else:
                            payload.details = f"{webnp['title']}"
                        payload.state = webnp['artist']
                        payload.small_image = self.config["other_media"][webnp["player"]]["icon"] # Set the small image defined for the player
                        if webnp["player"] == "Twitch" and webnp['artist'] is not None: # Hard coded stuff for twitch, giving a button for other people to click on to join the stream
                            media_button = {"label": "Watch on Twitch", "url": f"https://twitch.tv/{webnp['artist'].lower()}"}
                            payload.state = f"Watching {webnp['artist']} on Twitch"
                        elif webnp["player"] == "Youtube":
                            if webnp["cover"] != "":
                                try:
                                    video_id = webnp["cover"].split("/")[-2]
                                    media_button = {"label": "Watch on YouTube", "url": f"https://youtube.com/watch?v={video_id}"}
                                except IndexError:
                                    media_button = None
                        else:
                            media_button = None
                        duration_read = webnp["duration"].split(":")[::-1]
                        position_read = webnp["position"].split(":")[::-1]
                        duration = 0
                        position = 0
                        try:
                            for i in range(len(duration_read)-1, -1, -1): #Smart loop to convert times from hour/minutes to seconds. Fully expandable, so works with any lengths
                                duration += int(duration_read[i])*(60**i)
                            for i in range(len(position_read)-1, -1, -1):
                                position += int(position_read[i])*(60**i)
                        except ValueError:
                            formatted_exception = "Traceback (most recent call last):\n" + ''.join(format_tb(e.__traceback__)) + f"{type(e).__name__}: {e}"
                            self.log.warning(formatted_exception)
                        if self.config["use_time_left_media"] == True:
                            payload.end = int(time() + (duration - position))
                        else:
                            payload.start = int(time() - position)
        if self.config["show_games"]: # If we want any games to show
            processes.pop("vlc.exe", None)
            processes.pop("Plex.exe", None)
            if processes != {}:
                sorted_processes = sorted([p for p in list(processes.values())], key=lambda p: p["object"].pid, reverse=True) # Sort processes in order of oldest to newest using their PID
                process = sorted_processes[0]
                process_info = process["info"]
                if process_info["client_id"] == None: # If no client ID is provided, we want to disconnect the RPC instead
                    process_info["client_id"] = NoRPC
                if self.prev_cid != process_info["client_id"]: # Only log if the client ID has changed
                    self.log.debug(
                        f"Matched process {process['object'].name()} to client ID {process_info['client_id']} with name {process_info['name']}")
                try:
                    create_time = process["object"].create_time() # Fetch how long the process has been running, so we can put it into the RPC.
                except OSError:
                    # system processes, using boot time instead
                    create_time = boot_time()
                epoch = time() - create_time # Then format it into a human reaable format
                conv = {
                    "days": str(epoch // 86400).split('.')[0],
                    "hours": str(epoch // 3600 % 24).split('.')[0],
                    "minutes": str(epoch // 60 % 60).split('.')[0],
                    "seconds": str(epoch % 60).split('.')[0],
                    "full": strftime('%Y-%m-%d %I:%M:%S %p %Z', localtime(create_time))
                }
                time_info = f"for {conv['days'] if conv['days'] != '0' else ''}{'' if conv['days'] == '0' else 'd, '}{conv['hours'] if conv['hours'] != '0' else ''}{'' if conv['hours'] == '0' else 'h, '}{conv['minutes'] if conv['minutes'] != '0' else ''}{'' if conv['minutes'] == '0' else 'm'}"

                client_id = process_info["client_id"] # Everything worked, set the client id
                payload.details = f"{time_info}"
                payload.small_image = process_info.get("icon", None)

        # if [media_button, extra_button] != [None, None]: # Add any button that isn't a nonetype
        #     payload.buttons = []
        if media_button is not None:
            payload.add_button(media_button)
        if extra_button is not None:
            payload.add_button(extra_button)

        return client_id, payload

    def main(self):
        client_id, payload = self.get_payload() # Fetch what Client ID we should be using, and the payload data
        if self.prev_cid != client_id:
            self.log.info(f"Switching from {self.prev_cid} to {client_id}") 
            self.prev_cid = client_id # We changed client ID, reconnect the RPC with the new client ID
            if client_id != NoRPC:
                self.reconnect(client_id=client_id)
            else:
                if self.connected: # If we don't have a client ID, just clear the presence
                    self.RPC.clear()
        if not self.connected and client_id != NoRPC: # If for some reason the RPC isn't connected and we have a client id, connect
            self.reconnect(client_id=client_id)
        if not self.same_payload(payload) or self.last_update+300 < time(): # Check if payloads are the same, and if not, push and update
            self.previous_payload = payload
            self.log.debug(f"Setting presence with payload {payload}")
            if client_id != NoRPC:
                while True:
                    try:
                        self.RPC.update(**payload.to_dict())
                        self.last_update = time()
                    # Errors usually occur if discord is restarted or killed and we try to update the RPC, 
                    # and since we don't have any other way to check, this is where the errors happen
                    except InvalidID: 
                        self.log.warning("Invalid ID, restarting...")
                        self.reconnect(client_id=client_id)
                    except InvalidPipe:
                        self.log.warning("InvalidPipe, is discord running? Reconnecting...")
                        self.reconnect(client_id=client_id)
                    else:
                        break
            sleep(REFRESH_TIME_SECONDS) # Sleep for 15 seconds, since we can only update the rich presence every 15 seconds
        else:
            #self.log.debug("Ignoring same payload")
            sleep(REFRESH_TIME_SECONDS) # We don't want to constantly try fetch a new payload if nothing has changed, so only sleep for 5 seconds
        try:
            with open(f"{getcwd()}/config.json") as f: # Reread the config file to see if it has been updated, ignore if errors
                self.config = j_load(f)
        except JSONDecodeError:
            self.log.warning("Error reading config file")
        except FileNotFoundError:
            self.log.warning("Error reading config file")

    def close(self, signal, frame): # Properly shutdown the rich presence, disconnecting cleanly. Not sure if this even works
        self.playlists.save()
        self.log.info("Stopping...")
        try:
            self.RPC.close()
        except AttributeError:
            pass
        sys.exit()

    def get_traceback(self) -> str:
        return "Traceback (most recent call last):\n" + ''.join(format_tb(e.__traceback__)) + f"{type(e).__name__}: {e}"

if __name__ == "__main__":
    thread = Thread(target=client_init, daemon=True)
    thread.start()
    rpc = CustomRPC()
    while True:
        try:
            rpc.main()
        except Exception as e: # If any exceptions occur, try log them
            rpc.log.error(rpc.get_traceback())
