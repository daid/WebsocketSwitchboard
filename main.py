import http.server
import http
import sys
import threading
import time
import os
import shutil
import secrets
import math
import logging

import websocketHttp

_print = print
def print(*args):
    _print(*args)
    sys.stdout.flush()

SECRET_MASTER_AUTH_KEY = b"VERY_SECRET_KEY"


## WebSocketSwitchboard proxy.
#   The switchboard allows games to connect to the switchboard to a single websocket connection.
#       The switchboard will give the game an ID to display to the users on how to connect to the game.
#           http://[IP]/game/master
#   Clients can then connect to the switchboard webserver and fill in the ID.
#       The switchboard provides a channel to request static http files from the game trough the websocket and cache them, to serve them to the client
#           http://[IP]/game/[ID]/[path_of_static_asset]
#       The switchboard provides a websocket channel to connect clients connected to the switchboard to the game.
#           ws://[IP]/game/[ID]
#   Communication with game websocket: (>to game <from game
#       >AUTH
#       <AUTH:[SECRET]
#       >ID:[ID]
#       >GET:[asset_path]
#       <GET:[asset_path]\n[asset_contents]
#       >WS-OPEN:[WS-ID]
#       >WS-[WS-ID]:[WS message]
#       <WS-[WS-ID]:[WS message]
#       >WS-CLOSE:[WS-ID]

class GameSession:
    KEY_LENGTH = 5
    KEY_CHARS = b"ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    def __init__(self, websocket):
        self.key = b""
        for n in range(self.KEY_LENGTH):
            self.key += bytes([secrets.choice(self.KEY_CHARS)])
        self.master_websocket = websocket
        self.websockets = {}


class HTTPRequestHandler(websocketHttp.WebsocketMixin, http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/":
            return self.sendStaticFile("www/index.html")
        return self.sendFileNotFound()

    def sendStaticFile(self, path):
        self.send_response(http.HTTPStatus.OK)
        try:
            f = open(path, "rb")
        except IOError:
            return self.sendFileNotFound()
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

    def do_NEW_WEBSOCKET(self):
        self.server.active_websockets.add(self)
        if not self.path.startswith("/game/"):
            logging.warning("Incorrect websocket path: %s", self.path)
            self.rfile.close()
            return

        if self.path == "/game/master":
            self.game = None
            self.send_websocket(b"AUTH")
        else:
            game_id = self.path[6:].encode("utf-8")
            try:
                self.game = self.server.game_sessions[game_id]
                self.game.master_websocket.send_websocket(b"WS-OPEN:" + str(id(self)).encode("utf-8"))
                self.game.websockets[id(self)] = self
            except KeyError:
                logging.warning("Failed to find game: %s", game_id)
                self.rfile.close()

    def do_WEBSOCKET(self, message):
        if self.path == "/game/master":
            if self.game is None:
                # No game yet, expect an AUTH reply.
                if message == b"AUTH:" + SECRET_MASTER_AUTH_KEY:
                    self.game = GameSession(self)
                    self.send_websocket(b"ID:" + self.game.key)
                    self.server.game_sessions[self.game.key] = self.game
                else:
                    self.rfile.close()
            elif message.startswith(b"WS-"):
                ws_id = int(message[3:message.find(b":")])
                try:
                    self.game.websockets[ws_id].send_websocket(message[message.find(b":")+1:])
                except:
                    pass
            else:
                logging.warning("Unknown master command: %s", message)
        elif self.game is not None:
            self.game.master_websocket.send_websocket(b"WS-" + str(id(self)).encode("utf-8") + b":" + message)
    
    def do_CLOSE_WEBSOCKET(self):
        if self.path == "/game/master":
            if self.game:
                del self.server.game_sessions[self.game.key]
                for ws in self.game.websockets:
                    try:
                        ws.rfile.close()
                    except:
                        pass
        elif self.game:
            del self.game.websockets[id(self)]
            self.game.master_websocket.send_websocket(b"WS-CLOSE:" + str(id(self)).encode("utf-8"))
        self.server.active_websockets.remove(self)


class Server(http.server.ThreadingHTTPServer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.active_websockets = set()
        self.game_sessions = {}
        
        threading.Thread(target=self.__pingWebsockets, daemon=True).start()

    def __pingWebsockets(self):
        while True:
            time.sleep(5)
            try:
                for ws in self.active_websockets.copy():
                    ws.send_websocket_ping()
            except:
                pass


httpd = Server(('', 8000), HTTPRequestHandler)
httpd.serve_forever()
