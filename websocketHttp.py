import websocket
import threading
import http
import base64
import hashlib


class WebsocketMixin:
    timeout = 60.0

    def parse_request(self):
        if not super().parse_request():
            return False
        
        if "Connection" in self.headers and "Upgrade" in self.headers and "Sec-Websocket-Version" in self.headers and "Sec-Websocket-Key" in self.headers:
            if self.headers["Connection"].lower() != "upgrade" or self.headers["Upgrade"].lower() != "websocket" or self.headers["Sec-Websocket-Version"] != "13":
                self.send_error(http.HTTPStatus.BAD_REQUEST, "Bad Websocket upgrade request")
                return False

            self.send_response(http.HTTPStatus.SWITCHING_PROTOCOLS)
            self.send_header("Connection", "Upgrade")
            self.send_header("Upgrade", "websocket")
            self.send_header("Sec-WebSocket-Accept", base64.b64encode(hashlib.sha1((self.headers["sec-websocket-key"] + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("utf-8")).digest()).decode("utf-8"))
            if "Sec-WebSocket-Protocol" in self.headers:
                self.send_header("Sec-WebSocket-Protocol", "chat")
            self.end_headers()
            self.close_connection = True
            
            self.__handle_websocket()
            return False
        return True

    def __handle_websocket(self):
        self.__lock = threading.Lock()
        full_message = b''
        self.do_NEW_WEBSOCKET()
        try:
            while True:
                frame = websocket.readFrame(self.rfile)
                if frame is None:
                    return
                fin, opcode, message = frame
                if opcode == websocket.opcode_continuation:
                    full_message = full_message + message
                    if fin:
                        self.do_WEBSOCKET(full_message)
                        full_message = b''
                elif opcode == websocket.opcode_text or opcode == websocket.opcode_binary:
                    if fin:
                        self.do_WEBSOCKET(message)
                    else:
                        full_message = message
                elif opcode == websocket.opcode_close:
                    websocket.writeFrame(self.wfile, websocket.opcode_close, message)
                    return
                elif opcode == websocket.opcode_ping:
                    websocket.writeFrame(self.wfile, websocket.opcode_pong, message)
                elif opcode == websocket.opcode_pong:
                    pass
                else:
                    return
        except IOError:
            pass    # We can pretty much except an IOError at some point because the other side will close the connection, or we will get a timeout.
        finally:
            self.do_CLOSE_WEBSOCKET()

    def send_websocket(self, message):
        with self.__lock:
            websocket.writeFrame(self.wfile, websocket.opcode_text, message)

    def send_websocket_ping(self):
        with self.__lock:
            websocket.writeFrame(self.wfile, websocket.opcode_ping, b'')
