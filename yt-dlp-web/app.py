from flask import Flask, request, jsonify, render_template, send_from_directory
from flask_socketio import SocketIO, join_room, leave_room
import subprocess
import os
import uuid
import time
import threading

app = Flask(__name__)  # 创建 Flask 应用

# 启用 WebSocket，用于实时捕获 yt-dlp 的日志信息，WebSocket 发送日志到前端
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# 设置下载目录
DOWNLOAD_FOLDER = os.path.join(os.path.dirname(__file__), "downloads")
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)  # 如果目录不存在，则创建

# 存储 session_id 到 socket 连接的映射
user_sessions = {}

####################
#                  #
##### 主页路由 #####
#                  #
####################
@app.route('/')  
def index():
    return render_template('index.html')  # 渲染前端 HTML 页面

######################################
#                                    #
#####  让前端可以访问下载的文件  #####
#                                    #
######################################

@app.route('/downloads/<filename>')  # 允许前端访问下载的文件
def downloaded_file(filename):
    return send_from_directory(DOWNLOAD_FOLDER, filename, as_attachment=True)

######################################
#                                    #
#####  处理下载请求的 API 路由  #####
#                                    #
######################################

# 存储用户的下载进程和文件信息
user_downloads = {}

@app.route('/download', methods=['POST'])
def download_video():
    data = request.get_json()
    url = data.get('url')
    session_id = data.get('session_id')
    quality = data.get('quality')  # 获取画质参数
    resolution_map = {
        "best": "最佳画质",
        "worst": "最低画质",
        "137": "1080p",
        "136": "720p",
        "135": "480p"
    }
    resolution = resolution_map.get(quality, "未知画质")

    if not url or not session_id:
        return jsonify({"error": "URL 和 session_id 不能为空!"}), 400

    try:
        # 获取视频标题
        title_command = [os.path.join(os.path.dirname(__file__), "yt-dlp"), "--get-title", url]
        video_title = subprocess.check_output(title_command, text=True).strip()
        if resolution == "最佳画质":
            video_filename = f"{video_title}.%(ext)s"
        else:
            video_filename = f"{video_title}.{resolution}.%(ext)s"

        # 检查文件是否已存在
        existing_files = os.listdir(DOWNLOAD_FOLDER)
        existing_file = next((f for f in existing_files if f.startswith(video_title)), None)

        if existing_file:
            file_url = f"/downloads/{existing_file}"
            socketio.emit("log", {"message": f"检测到服务器内存在该视频的缓存，可以直接下载: {file_url}"}, room=session_id)
            socketio.emit("existing_file", {"file_url": file_url}, room=session_id)
            return jsonify({"message": "视频已存在，直接下载", "file_url": file_url})

        socketio.emit("log", {"message": "开始下载..."}, room=session_id)

        # 下载命令，添加画质选项
        command = [os.path.join(os.path.dirname(__file__), "yt-dlp"), "-f", quality, "-o", f"{DOWNLOAD_FOLDER}/{video_filename}", url]

        # 启动独立的 yt-dlp 进程
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        user_downloads[session_id] = {
            "process": process,
            "title": video_title
        }

        for line in iter(process.stdout.readline, ''):
            line = line.strip()
            if line:
                print(f"正在向{session_id}发送日志：", line)
                socketio.emit("log", {"message": line}, room=session_id)

        process.stdout.close()
        process.wait()

        # 查找下载的文件
        downloaded_files = os.listdir(DOWNLOAD_FOLDER)
        actual_filename = next((f for f in downloaded_files if f.startswith(video_title)), None)

        if actual_filename:
            file_url = f"/downloads/{actual_filename}"
            print(f"{session_id}的文件下载完成，发送地址:", file_url)
            socketio.emit("download_complete", {"file_url": file_url}, room=session_id)
            return jsonify({"message": "下载成功！", "url": url})
        else:
            return jsonify({"error": "下载失败，未找到文件"}), 500

    except subprocess.CalledProcessError as e:
        return jsonify({"error": "下载失败，请检查链接", "details": str(e)}), 500

####################
#  WebSocket 处理  #
####################
@socketio.on("connect")
def handle_connect():
    session_id = str(uuid.uuid4())  # 生成唯一 session_id
    user_sessions[request.sid] = session_id
    print(f"用户连接: {request.sid} -> session_id: {session_id}")
    join_room(session_id)
    socketio.emit("session_id", {"session_id": session_id}, room=request.sid)  # 发送 session_id 给前端

@socketio.on("disconnect")
def handle_disconnect():
    session_id = user_sessions.pop(request.sid, None)
    print(f"用户断开: {request.sid} -> session_id: {session_id}")


    if session_id in user_downloads:
            download_info = user_downloads.pop(session_id)
            process = download_info["process"]
            title = download_info["title"]

            # 如果进程仍在运行，则终止它
            if process.poll() is None:
                process.terminate()
                print(f"终止下载进程: {session_id}")

            # 设置定时器在10分钟后删除文件
            def delete_files():
                time.sleep(600)  # 10分钟
                downloaded_files = os.listdir(DOWNLOAD_FOLDER)
                for f in downloaded_files:
                    if f.startswith(title):
                        os.remove(os.path.join(DOWNLOAD_FOLDER, f))
                        print(f"删除文件: {f}")

            threading.Thread(target=delete_files).start()


if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)