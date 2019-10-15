import http.server
import http
import sys
import threading
import time
import os
import shutil
import random
import math
import logging
import struct
import json

import config
import websocketHttp
import rawsocketHttp

# pythons secrets library is introduced in 3.7, so use the system random instead (which is what secrets also uses)
secrets = random.SystemRandom()

## WebSocketSwitchboard proxy.
#   The Switchboard proxy allows clients and servers to connect to each other by using this proxy as middle man.
#       The switchboard serves two main goals:
#       - Listing online games, allowing clients to find the server.
#   There are two main ways of connecting to the server, by raw socket or by websocket.
#   Terminology:
#       - Server: The game server.
#       - Switchboard: This application.
#       - Client: Client games that want to connect to the server.
#       - Game session: An instance of a running server exposing itself to the world for connections.
#   Both options start as a http request to punch trough firewalls, and optionally support SSL connections.
#   The server first needs to register a game with a simple http POST call, which will tell the server the
#       "key" and "secret". The "key" should be shown to the users to allow connecting to the game.
#       The "secret" is for the server only to start new connections to the game from the server side.
#   API endpoints:
#       /game/register
#           As a server register a new game session, only POST requests can be done.
#           Requires following parameters:
#               - name: Name that the server wants to give to this game (string)
#               - game_name: Identifies the type of game (string)
#               - game_version: Identifies the version of the game (number)
#               - secret_hash: SHA1 hash of the nonce and a shared switchboard password (string)
#               - public: true if the game will be publicly listed (bool)
#               - address: ip addresses where the server thinks it runs. Useful if a client is in the same subnet and thus can directly connect. (list of strings)
#               - port: port at which the server runs the game, to see if we can directly connect instead of using the switchboard. (number)
#           Will reply with:
#               - key: Unique key for this game session. Only upper case and numbers for easy entry by a user. (string)
#               - secret: Unique secret for the server to identify itself for this game. (string)
#       /game/master
#           As a server connect as a websocket or raw socket and wait for a client websocket to connect.
#           Requires a GET with upgrade to websocket or raw.
#           Must supply the key and secret as HTTP headers.
#       /game/connect/[key]
#           As a client, connect to a game.
#               When connecting as a raw or websocket will make a direct transparent channel to a /game/master connected socket if available for the given [key].
#               Or returns a 404 if the game does not exist and a 503 if the server has no connected socket yet.
#           Or when using a normal http GET request, will return server info for this game.
#       /game/list/[game_name]
#           Get a list of public games for a specific game_name. The client should filter out incompattible versions,
#               but those are still listed to let the player know that they have the wrong version of the game.
#           Listed games are in json format and is reported as an array of objects with the following fields:
#           - name: Name of the server
#           - version: Version of the game, client must check for a match
#           - key: Unique game connect key for this session
#           - address: List of IP addresses for direct connection. (optional)
#           - port: port to connect to for direct connection. (optional)
#           Note that the reported addresses could be an external address detected by the switchboard or the internal addresses reported by the server
#               if the switchboard detects that both client and server have the same origin.

class GameSession:
    KEY_LENGTH = 5
    SECRET_LENGTH = 32
    KEY_CHARS = b"ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    def __init__(self, name, game_name, version, public, public_address, private_address, port):
        self.__lock = threading.Lock()
        self.__name = name
        self.__game_name = game_name
        self.__version = version
        self.__public = public
        self.__public_address = public_address
        self.__private_address = private_address
        self.__port = port
        self.__key = b""
        self.__secret = b""
        self.__waiting_websocket = None
        self.__waiting_rawsocket = None
        self.__timeout = time.monotonic() + 60.0
        
        for n in range(self.KEY_LENGTH):
            self.__key += bytes([secrets.choice(self.KEY_CHARS)])
        for n in range(self.SECRET_LENGTH):
            self.__secret += bytes([secrets.choice(self.KEY_CHARS)])

    @property
    def name(self):
        return self.__name

    @property
    def version(self):
        return self.__version

    @property
    def game_name(self):
        return self.__game_name

    @property
    def public(self):
        return self.__public

    def getAddressesFor(self, remote_address):
        if remote_address == self.__public_address:
            return self.__private_address + [self.__public_address]
        return [self.__public_address]

    @property
    def port(self):
        return self.__port
    
    @property
    def key(self):
        return self.__key

    @property
    def secret(self):
        return self.__secret

    def grabWebsocket(self):
        with self.__lock:
            result = self.__waiting_websocket
            self.__waiting_websocket = None
        return result
    
    def setWaitingWebsocket(self, socket):
        self.__timeout = time.monotonic() + 60.0
        with self.__lock:
            if self.__waiting_websocket is not None:
                self.__waiting_websocket.rfile.close()
            self.__waiting_websocket = socket

    def grabRawsocket(self):
        with self.__lock:
            result = self.__rawsocket_in_waiting
            self.__rawsocket_in_waiting = None
        return result
    
    def setWaitingRawsocket(self, socket):
        self.__timeout = time.monotonic() + 60.0
        with self.__lock:
            if self.__waiting_rawsocket is not None:
                self.__waiting_rawsocket.rfile.close()
            self.__waiting_rawsocket = socket

    def hasTimeout(self):
        if self.__waiting_websocket is not None:
            if not self.__waiting_websocket.rfile.closed:
                self.__timeout = time.monotonic() + 60.0
                return False
        if self.__waiting_rawsocket is not None:
            if not self.__waiting_rawsocket.rfile.closed:
                self.__timeout = time.monotonic() + 60.0
                return False
        return time.monotonic() > self.__timeout


class HTTPRequestHandler(rawsocketHttp.RawsocketMixin, websocketHttp.WebsocketMixin, http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/":
            return self.sendStaticFile("www/index.html")
        elif self.path.startswith("/game/list/"):
            game_name = self.path[11:]
            result = []
            for session in self.server.getGames(game_name):
                result.append({
                    "name": session.name,
                    "version": session.version,
                    "key": session.key.decode("ascii"),
                    "address": session.getAddressesFor(self.client_address[0]),
                    "port": session.port
                })
            return self.sendJson(result)
        elif self.path.startswith("/game/connect/"):
            game_key = self.path[14:]
            game = self.server.findGame(game_key)
            if game is None:
                return http.HTTPStatus.NOT_FOUND
            return self.sendJson({
                "name": game.name,
                "version": game.version,
                "key": game.key.decode("ascii"),
                "address": game.getAddressesFor(self.client_address[0]),
                "port": game.port
            })
        self.send_error(http.HTTPStatus.NOT_FOUND)

    def do_POST(self):
        if self.path == "/game/register":
            if "Content-Length" not in self.headers or "Content-Type" not in self.headers:
                self.send_error(http.HTTPStatus.BAD_REQUEST)
                return
            if self.headers["Content-Type"] != "application/json":
                self.send_error(http.HTTPStatus.BAD_REQUEST)
                return
            try:
                post_data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            except ValueError:
                self.send_error(http.HTTPStatus.BAD_REQUEST)
                return

            if "name" not in post_data or "game_name" not in post_data or "game_version" not in post_data or "secret_hash" not in post_data:
                self.send_error(http.HTTPStatus.BAD_REQUEST)
                return
            if  "public" not in post_data or "address" not in post_data or "port" not in post_data:
                self.send_error(http.HTTPStatus.BAD_REQUEST)
                return
            if not isinstance(post_data["address"], list):
                self.send_error(http.HTTPStatus.BAD_REQUEST)
                return
            for address in post_data["address"]:
                if not isinstance(address, str):
                    self.send_error(http.HTTPStatus.BAD_REQUEST)
                    return
            # TODO: Check secret hash
            game = GameSession(
                name = post_data["name"], 
                game_name = post_data["game_name"],
                version = int(post_data["game_version"]),
                public = bool(post_data["public"]),
                public_address = self.client_address[0],
                private_address = post_data["address"],
                port = int(post_data["port"])
            )
            if not self.server.addGame(game):
                # TODO, we should just retry.
                self.send_error(http.HTTPStatus.INTERNAL_SERVER_ERROR)
                return

            return self.sendJson({"key": game.key.decode("ascii"), "secret": game.secret.decode("ascii")})
        self.send_error(http.HTTPStatus.NOT_FOUND)

    def sendStaticFile(self, path):
        try:
            f = open(path, "rb")
        except IOError:
            return self.sendFileNotFound()
        self.send_response(http.HTTPStatus.OK)
        fs = os.fstat(f.fileno())
        self.send_header("Content-Length", str(fs.st_size))
        self.send_header("Last-Modified", self.date_time_string(fs.st_mtime))
        self.end_headers()
        shutil.copyfileobj(f, self.wfile)
        f.close()

    def sendJson(self, data):
        response = json.dumps(data)
        self.send_response(http.HTTPStatus.OK)
        self.send_header("Content-Length", len(response))
        self.end_headers()
        self.wfile.write(response.encode("ascii"))

    def __handleWebOrRawConnect(self, socket_type):
        if self.path == "/game/master":
            if not "Game-Key" in self.headers or not "Game-Secret" in self.headers:
                logging.warning("Master connection: No game or secret supplied")
                return http.HTTPStatus.BAD_REQUEST
            game_key = self.headers["Game-Key"]
            secret = self.headers["Game-Secret"]
            game = self.server.findGame(game_key)
            if game is None:
                logging.warning("Master connection: Game not found")
                return http.HTTPStatus.NOT_FOUND
            if game.secret != secret:
                logging.warning("Master connection: Secret mismatch")
                return http.HTTPStatus.BAD_REQUEST
            if socket_type == "Web":
                game.setWaitingWebsocket(self)
            else:
                game.setWaitingRawsocket(self)
            return
        elif self.path.startswith("/game/connect/"):
            game_key = self.path[14:]
            game = self.server.findGame(game_key)
            if game is None:
                return http.HTTPStatus.NOT_FOUND
            if socket_type == "Web":
                self.other = game.grabWebsocket()
            else:
                self.other = game.grabRawsocket()
            if self.other is None:
                return http.HTTPStatus.SERVICE_UNAVAILABLE
            self.other.other = self
            return
        return http.HTTPStatus.NOT_FOUND

    def do_WEBSOCKET(self):
        return self.__handleWebOrRawConnect("Web")

    def websocket_OPEN(self):
        if self.path == "/game/master":
            pass
        else:
            self.other.websocket_send(b"CLIENT_CONNECTED")

    def websocket_MESSAGE(self, message):
        if self.other is not None:
            self.other.websocket_send(message)
    
    def websocket_CLOSE(self):
        if self.other is not None:
            self.other.rfile.close()

    def do_RAW(self):
        return self.__handleWebOrRawConnect("Raw")

    def rawsocket_OPEN(self):
        if self.path == "/game/master":
            pass
        else:
            self.other.rawsocket_send(struct.pack("!I", 0))

    def rawsocket_MESSAGE(self, data):
        if self.other is not None:
            self.other.rawsocket_send(data)

    def rawsocket_CLOSE(self):
        if self.other is not None:
            self.other.rfile.close()


class Server(http.server.ThreadingHTTPServer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.game_sessions = {}

    def addGame(self, game):
        self.cleanTimeoutSessions()
        if game.key in self.game_sessions:
            return False
        self.game_sessions[game.key] = game
        return True

    def findGame(self, game_key):
        self.cleanTimeoutSessions()
        try:
            return self.game_sessions[game_key]
        except KeyError:
            return None

    def getGames(self, game_name):
        self.cleanTimeoutSessions()
        return [session for session in self.game_sessions.values() if session.public and session.game_name == game_name]

    def cleanTimeoutSessions(self):
        self.game_sessions = {k:v for (k,v) in self.game_sessions.items() if not v.hasTimeout()}

if __name__ == "__main__":
    httpd = Server(('', config.server_port), HTTPRequestHandler)
    httpd.serve_forever()
