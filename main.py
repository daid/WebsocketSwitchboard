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

import config
import websocketHttp
import rawsocketHttp


secrets = random.SystemRandom()

## WebSocketSwitchboard proxy.
#   The switchboard allows games to connect to the switchboard to a single websocket connection.
#       The switchboard will give the game an ID to display to the users on how to connect to the game.
#           http://[IP]/game/master
#   Clients can then connect to the switchboard webserver and fill in the ID.
#       The switchboard provides a websocket channel to connect clients connected to the switchboard to the game.
#           ws://[IP]/game/[GAME_ID]
#   Communication with game websocket: (>to game <from game
#       >AUTH
#       <AUTH:[SECRET]
#       >ID:[GAME_ID]:[GAME_SECRET]
#   Each time a client connects to the switchboard on a GAME_ID, the socket connection to the server will be deticated to that client, making a point to point connection.
#       >CLIENT_CONNECTED
#   The server is expected to connect again to the switchboard, and send:
#       >AUTH
#       <AUTH:[SECRET]:[GAME_SECRET]
#   This new connection will be used for the next connecting client.

class GameSession:
    KEY_LENGTH = 5
    SECRET_LENGTH = 32
    KEY_CHARS = b"ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    def __init__(self):
        self.__lock = threading.Lock()
        self.__key = b""
        self.__server_secret = b""
        self.__websocket_socket_in_waiting = None
        self.__rawsocket_in_waiting = None
        self.__timeout = time.monotonic() + 60.0
        
        for n in range(self.KEY_LENGTH):
            self.__key += bytes([secrets.choice(self.KEY_CHARS)])
        for n in range(self.SECRET_LENGTH):
            self.__server_secret += bytes([secrets.choice(self.KEY_CHARS)])
    
    @property
    def key(self):
        return self.__key

    @property
    def server_secret(self):
        return self.__server_secret
    
    def grabWebsocket(self):
        self.__timeout = time.monotonic() + 60.0
        with self.__lock:
            result = self.__websocket_socket_in_waiting
            self.__websocket_socket_in_waiting = None
        return result
    
    def setWaitingWebsocket(self, socket):
        self.__timeout = time.monotonic() + 60.0
        with self.__lock:
            if self.__websocket_socket_in_waiting is not None:
                self.__websocket_socket_in_waiting.rfile.close()
            self.__websocket_socket_in_waiting = socket

    def grabRawsocket(self):
        self.__timeout = time.monotonic() + 60.0
        with self.__lock:
            result = self.__rawsocket_in_waiting
            self.__rawsocket_in_waiting = None
        return result
    
    def setWaitingRawsocket(self, socket):
        self.__timeout = time.monotonic() + 60.0
        with self.__lock:
            if self.__rawsocket_in_waiting is not None:
                self.__rawsocket_in_waiting.rfile.close()
            self.__rawsocket_in_waiting = socket

    def hasTimeout(self):
        if self.__websocket_socket_in_waiting is not None:
            if not self.__websocket_socket_in_waiting.rfile.closed:
                self.__timeout = time.monotonic() + 60.0
                return False
        if self.__rawsocket_in_waiting is not None:
            if not self.__rawsocket_in_waiting.rfile.closed:
                self.__timeout = time.monotonic() + 60.0
                return False
        return time.monotonic() > self.__timeout


class HTTPRequestHandler(rawsocketHttp.RawsocketMixin, websocketHttp.WebsocketMixin, http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/":
            return self.sendStaticFile("www/index.html")
        return self.sendFileNotFound()

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

    def sendFileNotFound(self):
        self.send_response(http.HTTPStatus.NOT_FOUND)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_WEBSOCKET(self):
        if not self.path.startswith("/game/"):
            logging.warning("Incorrect websocket path: %s", self.path)
            return False

        if self.path == "/game/master":
            self.game = None
            self.other = None
        else:
            game_id = self.path[6:].encode("utf-8")
            try:
                game = self.server.game_sessions[game_id]
            except KeyError:
                logging.warning("Failed to find game: %s", game_id)
                return False
            else:
                self.other = game.grabWebsocket()
                if self.other is None:
                    logging.warning("No socket available to grab.")
                    return False
                self.other.other = self
        return True

    def websocket_OPEN(self):
        if self.path == "/game/master":
            self.websocket_send(b"AUTH")
        else:
            self.other.websocket_send(b"CLIENT_CONNECTED")

    def websocket_MESSAGE(self, message):
        if self.other is not None:
            self.other.websocket_send(message)
        elif self.path == "/game/master":
            parts = message.split(b":")
            if self.game is None and parts[0] == b"AUTH":
                # No game yet, expect an AUTH reply.
                if len(parts) > 2 and parts[1] == config.server_shared_secret:
                    for game in self.server.game_sessions.values():
                        if game.server_secret == parts[2]:
                            self.game = game
                            break
                    if game is not None:
                        self.game.setWaitingWebsocket(self)
                    else:
                        self.rfile.close()
                elif len(parts) > 1 and parts[1] == config.server_shared_secret:
                    self.game = GameSession()
                    self.game.setWaitingWebsocket(self)
                    self.server.cleanTimeoutSessions()
                    self.server.game_sessions[self.game.key] = self.game
                    self.websocket_send(b"ID:" + self.game.key + b":" + self.game.server_secret)
                else:
                    self.rfile.close()
            else:
                self.rfile.close()
    
    def websocket_CLOSE(self):
        if self.other is not None:
            self.other.rfile.close()

    def do_RAW(self):
        if not self.path.startswith("/game/"):
            logging.warning("Incorrect rawsocket path: %s", self.path)
            return False

        if self.path == "/game/master":
            if "Shared-secret" in self.headers and self.headers["Shared-secret"].encode("utf-8") == config.server_shared_secret:
                if "Server-secret" in self.headers:
                    for game in self.server.game_sessions.values():
                        if game.server_secret == self.headers["Server-secret"]:
                            self.game = game
                            break
                    if game is not None:
                        self.game.setWaitingRawsocket(self)
                    else:
                        return False
                else:
                    self.game = GameSession()
                    self.game.setWaitingRawsocket(self)
                    self.server.cleanTimeoutSessions()
                    self.server.game_sessions[self.game.key] = self.game
            else:
                return False
        else:
            game_id = self.path[6:].encode("utf-8")
            try:
                game = self.server.game_sessions[game_id]
            except KeyError:
                logging.warning("Failed to find game: %s", game_id)
                return False
            else:
                self.other = game.grabRawsocket()
                if self.other is None:
                    logging.warning("No socket available to grab.")
                    return False
                self.other.other = self
        return True

    def rawsocket_OPEN(self):
        if self.path == "/game/master":
            self.other = None
            self.rawsocket_send(struct.pack("!II5sI32s", 4 + 5 + 4 + 32, 5, self.game.key, 32, self.game.server_secret))
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

    def cleanTimeoutSessions(self):
        self.game_sessions = {k:v for (k,v) in self.game_sessions.items() if not v.hasTimeout()}

if __name__ == "__main__":
    httpd = Server(('', config.server_port), HTTPRequestHandler)
    httpd.serve_forever()
