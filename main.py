import http.server
import http
import sys
import threading
import time
import os
import shutil

import websocketHttp

_print = print
def print(*args):
    _print(*args)
    sys.stdout.flush()


## WebSocketSwitchboard proxy.
#   The switchboard allows games to connect to the switchboard to a single websocket connection.
#       The switchboard will give the game an ID to display to the users on how to connect to the game.
#   Clients can then connect to the switchboard webserver and fill in the ID.
#       The switchboard provides a channel to request static http files from the game trough the websocket and cache them, to serve them to the client
#           http://[IP]/game/[ID]/[path_of_static_asset]
#       The switchboard provides a websocket channel to connect clients connected to the switchboard to the game.
#           http://[IP]/game/[ID]/[path_of_websocket]
#   Communication with game websocket: (>to game <from game
#       >ID:[ID]
#       >GET:[asset_path]
#       <GET:[asset_path]\n[asset_contents]
#       >WS-OPEN:[WS-ID]/[WS-PATH]
#       >WS-[WS-ID]:[WS message]
#       <WS-[WS-ID]:[WS message]
#       >WS-CLOSE:[WS-ID]/[WS-PATH]

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
        print("NEW WS", self.path)
        self.server.active_websockets.add(self)

    def do_WEBSOCKET(self, message):
        print("WS:", self.path, message)
        self.send_websocket(b"ECHO:" + message)
    
    def do_CLOSE_WEBSOCKET(self):
        print("CLOSE WS")
        self.server.active_websockets.remove(self)


class Server(http.server.ThreadingHTTPServer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.active_websockets = set()
        
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
