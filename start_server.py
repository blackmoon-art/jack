#!/usr/bin/env python3
import http.server
import socketserver
import os

os.chdir(os.path.dirname(os.path.abspath(__file__)))
PORT = 8080

Handler = http.server.SimpleHTTPRequestHandler
with socketserver.TCPServer(("", PORT), Handler) as httpd:
    print(f"🎮 超级玛丽游戏已启动!")
    print(f"🌐 请在浏览器中打开: http://localhost:{PORT}/supermario.html")
    print(f"📱 本机IP访问: http://0.0.0.0:{PORT}/supermario.html")
    print(f"按 Ctrl+C 停止服务器")
    httpd.serve_forever()
