import threading
import http
import base64
import hashlib
import threading
import time


class RawsocketMixin:
    def __init__(self, *args):
        super().__init__(*args)
        self.__is_raw = False

    def parse_request(self):
        if not super().parse_request():
            return False
        
        if "Connection" in self.headers and "Upgrade" in self.headers and self.headers["Connection"].lower() == "upgrade" and self.headers["Upgrade"].lower() == "raw":
            if not self.do_RAW():
                self.send_error(http.HTTPStatus.BAD_REQUEST, "Bad raw upgrade request")
                return False

            self.send_response(http.HTTPStatus.SWITCHING_PROTOCOLS)
            self.send_header("Connection", "Upgrade")
            self.send_header("Upgrade", "raw")
            self.send_header("Cache-Control", "No-Store")
            self.end_headers()
            self.close_connection = True
            
            self.__handle_rawsocket()
            return False
        return True

    def __handle_rawsocket(self):
        self.__lock = threading.Lock()
        self.connection.settimeout(60.0 * 60.0)
        self.__is_raw = True
        self.rawsocket_OPEN()
        try:
            while not self.rfile.closed:
                message = self.connection.recv(4096)
                if message == b"":
                    return
                self.rawsocket_MESSAGE(message)
        except IOError:
            pass    # We can pretty much except an IOError at some point because the other side will close the connection, or we will get a timeout.
        finally:
            self.rawsocket_CLOSE()
            self.__is_raw = False

    def is_raw(self):
        return self.__is_raw

    def rawsocket_send(self, message):
        if not self.wfile.closed:
            self.wfile.write(message)
