import websocketParse
import threading
import http
import base64
import hashlib
import threading
import time


class WebsocketMixin:
    timeout = 60.0
    __ping_thread = None
    __active_websockets = set()

    def __init__(self, *args):
        super().__init__(*args)
        
        self.__lock = None
        self.__is_websocket = False
        
        if WebsocketMixin.__ping_thread is None:
            __ping_thread = threading.Thread(target=self.__pingWebsockets, daemon=True)
            __ping_thread.start()

    def parse_request(self):
        if not super().parse_request():
            return False
        
        if "Connection" in self.headers and "Upgrade" in self.headers and "Sec-Websocket-Version" in self.headers and "Sec-Websocket-Key" in self.headers:
            if self.headers["Connection"].lower() != "upgrade" or self.headers["Upgrade"].lower() != "websocket" or self.headers["Sec-Websocket-Version"] != "13":
                self.send_error(http.HTTPStatus.BAD_REQUEST, "Bad Websocket upgrade request")
                return False
            
            if not self.do_WEBSOCKET():
                self.send_error(http.HTTPStatus.BAD_REQUEST, "Bad Websocket upgrade request")
                return False

            self.send_response(http.HTTPStatus.SWITCHING_PROTOCOLS)
            self.send_header("Connection", "Upgrade")
            self.send_header("Upgrade", "websocket")
            self.send_header("Cache-Control", "No-Cache")
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
        WebsocketMixin.__active_websockets.add(self)
        self.__is_websocket = True
        self.websocket_OPEN()
        full_message = b''
        try:
            while not self.rfile.closed:
                frame = websocketParse.readFrame(self.rfile)
                if frame is None:
                    return
                fin, opcode, message = frame
                if opcode == websocketParse.opcode_continuation:
                    full_message = full_message + message
                    if fin:
                        self.websocket_MESSAGE(full_message)
                        full_message = b''
                elif opcode == websocketParse.opcode_text or opcode == websocketParse.opcode_binary:
                    if fin:
                        self.websocket_MESSAGE(message)
                    else:
                        full_message = message
                elif opcode == websocketParse.opcode_close:
                    websocketParse.writeFrame(self.wfile, websocketParse.opcode_close, message)
                    return
                elif opcode == websocketParse.opcode_ping:
                    websocketParse.writeFrame(self.wfile, websocketParse.opcode_pong, message)
                elif opcode == websocketParse.opcode_pong:
                    pass
                else:
                    return
        except IOError:
            pass    # We can pretty much except an IOError at some point because the other side will close the connection, or we will get a timeout.
        finally:
            WebsocketMixin.__active_websockets.remove(self)
            self.websocket_CLOSE()
            self.__is_websocket = False

    def websocket_send(self, message):
        with self.__lock:
            if not self.wfile.closed:
                websocketParse.writeFrame(self.wfile, websocketParse.opcode_text, message)

    def websocket_send_ping(self):
        with self.__lock:
            if not self.wfile.closed:
                websocketParse.writeFrame(self.wfile, websocketParse.opcode_ping, b'')

    def is_websocket(self):
        return self.__is_websocket

    @staticmethod
    def __pingWebsockets():
        while True:
            time.sleep(5)
            try:
                for ws in WebsocketMixin.__active_websockets.copy():
                    ws.websocket_send_ping()
            except:
                pass
