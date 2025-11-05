#!/usr/bin/env python3
"""
YouTube Uploader Flask Application with Real Upload
"""

import os
import json
import threading
import time
from datetime import datetime
from flask import Flask, render_template, request, jsonify, redirect, url_for, session
from flask_socketio import SocketIO, emit
import subprocess
import uuid
import logging
from video_processor import VideoProcessor

# Google OAuth imports
try:
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import Flow
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    GOOGLE_AVAILABLE = True
    
    # Allow insecure transport for local development
    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
    
except ImportError:
    GOOGLE_AVAILABLE = False
    print("Warning: Google API libraries not available")

app = Flask(__name__)
app.config['SECRET_KEY'] = 'yt-uploader-secret-key'
socketio = SocketIO(app, cors_allowed_origins="*")

# Configuration
CLIENT_SECRETS_FILE = "client_secrets.json"
SCOPES = ['https://www.googleapis.com/auth/youtube.upload']
CONFIG_FILE = 'config.json'
HISTORY_FILE = 'history.json'

# Global variables
upload_queue = []
upload_status = {}
upload_paused = False

# Global video processor instance
video_processor = VideoProcessor()

def load_config():
    """Load configuration from JSON file"""
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    return {
        'upload_folder': '',
        'log_folder': '',
        'youtube_credentials': None,
        'upload_preferences': {
            'default_privacy': 'public',
            'default_tags': 'shorts, upload, automated',
            'upload_delay': 10
        }
    }

def save_config(config):
    """Save configuration to JSON file"""
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2)

def load_history():
    """Load upload history from JSON file"""
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, 'r') as f:
            return json.load(f)
    return []

def save_history(history):
    """Save upload history to JSON file"""
    with open(HISTORY_FILE, 'w') as f:
        json.dump(history, f, indent=2)

def get_youtube_service():
    """Get authenticated YouTube service"""
    config = load_config()
    creds_data = config.get('youtube_credentials')
    
    if not creds_data:
        return None
    
    credentials = Credentials(
        token=creds_data['token'],
        refresh_token=creds_data.get('refresh_token'),
        token_uri=creds_data['token_uri'],
        client_id=creds_data['client_id'],
        client_secret=creds_data['client_secret'],
        scopes=creds_data['scopes']
    )
    
    return build('youtube', 'v3', credentials=credentials)

@app.route('/')
def index():
    config = load_config()
    return render_template('index.html', config=config)

@app.route('/upload')
def upload():
    return render_template('upload.html')

@app.route('/history')
def history():
    history_data = load_history()
    return render_template('history.html', history=history_data)

@app.route('/settings')
def settings():
    config = load_config()
    return render_template('settings.html', config=config)

@app.route('/api/save_settings', methods=['POST'])
def save_settings():
    """Save application settings"""
    data = request.json
    config = load_config()
    
    config['upload_folder'] = data.get('upload_folder', '')
    config['log_folder'] = data.get('log_folder', '')
    
    save_config(config)
    return jsonify({'success': True})

@app.route('/api/save_preferences', methods=['POST'])
def save_preferences():
    """Save upload preferences"""
    data = request.json
    config = load_config()
    
    config['upload_preferences'] = {
        'default_privacy': data.get('default_privacy', 'public'),
        'default_tags': data.get('default_tags', 'shorts, upload, automated'),
        'upload_delay': int(data.get('upload_delay', 10))
    }
    
    save_config(config)
    return jsonify({'success': True})

@app.route('/api/oauth_start')
def oauth_start():
    """Start OAuth flow"""
    if not GOOGLE_AVAILABLE:
        return jsonify({'error': 'Google API libraries not installed'}), 500
    
    if not os.path.exists(CLIENT_SECRETS_FILE):
        return jsonify({'error': 'client_secrets.json not found'}), 500
    
    try:
        flow = Flow.from_client_secrets_file(
            CLIENT_SECRETS_FILE,
            scopes=SCOPES
        )
        
        flow.redirect_uri = 'http://localhost:5000/oauth/callback'
        
        auth_url, state = flow.authorization_url(
            access_type='offline',
            include_granted_scopes='true'
        )
        
        session['oauth_state'] = state
        
        return redirect(auth_url)
    
    except Exception as e:
        return jsonify({'error': f'OAuth setup failed: {str(e)}'}), 500

@app.route('/oauth/callback')
def oauth_callback():
    """Handle OAuth callback"""
    if not GOOGLE_AVAILABLE:
        return "Google API libraries not available", 500
    
    try:
        flow = Flow.from_client_secrets_file(
            CLIENT_SECRETS_FILE,
            scopes=SCOPES,
            state=session.get('oauth_state')
        )
        
        flow.redirect_uri = 'http://localhost:5000/oauth/callback'
        
        flow.fetch_token(authorization_response=request.url)
        
        credentials = flow.credentials
        
        # Save credentials
        config = load_config()
        config['youtube_credentials'] = {
            'token': credentials.token,
            'refresh_token': credentials.refresh_token,
            'token_uri': credentials.token_uri,
            'client_id': credentials.client_id,
            'client_secret': credentials.client_secret,
            'scopes': credentials.scopes
        }
        save_config(config)
        
        return """
        <html>
        <body style="background: #1a1a1a; color: #e0e0e0; font-family: Arial;">
        <div style="text-align: center; padding: 50px;">
            <h2 style="color: #4CAF50;">✓ Authentication Successful!</h2>
            <p>YouTube account connected successfully.</p>
            <p>You can close this window and return to the application.</p>
            <script>
            setTimeout(function() {
                window.close();
            }, 3000);
            </script>
        </div>
        </body>
        </html>
        """
        
    except Exception as e:
        return """
        <html>
        <body style="background: #1a1a1a; color: #e0e0e0; font-family: Arial;">
        <div style="text-align: center; padding: 50px;">
            <h2 style="color: #f44336;">✗ Authentication Failed</h2>
            <p>Error: """ + str(e) + """</p>
            <script>
            setTimeout(function() {
                window.close();
            }, 5000);
            </script>
        </div>
        </body>
        </html>
        """, 500

@app.route('/api/oauth_status')
def oauth_status():
    """Check OAuth completion status"""
    config = load_config()
    has_credentials = config.get('youtube_credentials') is not None
    
    return jsonify({
        'completed': has_credentials,
        'success': has_credentials
    })

@app.route('/api/disconnect_youtube', methods=['POST'])
def disconnect_youtube():
    """Disconnect YouTube account"""
    config = load_config()
    config['youtube_credentials'] = None
    save_config(config)
    return jsonify({'success': True})

@app.route('/api/scan_folder', methods=['POST'])
def scan_folder():
    """Scan folder for videos with duplicate detection and aspect ratio checking"""
    data = request.json
    folder_path = data.get('folder_path', '').strip()
    
    print(f"Scanning folder: {folder_path}")
    
    if not folder_path:
        return jsonify({'success': False, 'error': 'No folder path provided'})
    
    # Expand user path (~)
    folder_path = os.path.expanduser(folder_path)
    
    if not os.path.exists(folder_path):
        return jsonify({'success': False, 'error': f'Folder not found: {folder_path}'})
    
    if not os.path.isdir(folder_path):
        return jsonify({'success': False, 'error': f'Path is not a directory: {folder_path}'})
    
    # Load upload history for duplicate checking
    history = load_history()
    uploaded_files = {item['filename']: item for item in history if item.get('status') == 'completed'}
    failed_files = {item['filename']: item for item in history if item.get('status') == 'failed'}
    
    print(f"Loaded history: {len(uploaded_files)} successful uploads, {len(failed_files)} failed uploads")
    
    video_extensions = ['.mp4', '.mov', '.avi', '.wmv', '.flv', '.webm', '.mkv']
    videos = []
    
    try:
        files = os.listdir(folder_path)
        print(f"Found {len(files)} files in directory")
        
        for filename in files:
            if any(filename.lower().endswith(ext) for ext in video_extensions):
                video_path = os.path.join(folder_path, filename)
                print(f"Processing video file: {filename}")
                
                # Check if file was already uploaded successfully
                is_duplicate = filename in uploaded_files
                is_failed = filename in failed_files
                
                # Get video info for aspect ratio checking
                video_info = video_processor.get_video_info(video_path)
                aspect_ratio_ok = False
                needs_cropping = False
                
                if video_info:
                    current_ratio = video_info['aspect_ratio']
                    target_ratio = 9/16
                    aspect_ratio_ok = abs(current_ratio - target_ratio) < 0.01
                    needs_cropping = not aspect_ratio_ok
                
                # Basic validation
                valid = True
                message = "Ready to upload"
                status_type = "new"
                youtube_url = None
                upload_date = None
                
                if is_duplicate:
                    # File was already uploaded successfully
                    valid = False  # Don't include in upload by default
                    status_type = "duplicate"
                    upload_info = uploaded_files[filename]
                    upload_date = upload_info.get('upload_date', '')
                    youtube_url = upload_info.get('youtube_url', '')
                    
                    # Format date nicely
                    if upload_date:
                        try:
                            date_obj = datetime.fromisoformat(upload_date.replace('Z', '+00:00'))
                            formatted_date = date_obj.strftime('%b %d, %Y')
                        except:
                            formatted_date = upload_date[:10]
                    else:
                        formatted_date = 'Unknown date'
                    
                    message = f"Already uploaded ({formatted_date})"
                    
                elif is_failed:
                    # File had a failed upload - allow retry
                    valid = True
                    status_type = "retry"
                    upload_info = failed_files[filename]
                    upload_date = upload_info.get('upload_date', '')
                    
                    if upload_date:
                        try:
                            date_obj = datetime.fromisoformat(upload_date.replace('Z', '+00:00'))
                            formatted_date = date_obj.strftime('%b %d, %Y')
                        except:
                            formatted_date = upload_date[:10]
                    else:
                        formatted_date = 'Unknown date'
                    
                    message = f"Previous upload failed ({formatted_date}) - Ready to retry"
                    
                elif not aspect_ratio_ok:
                    # Wrong aspect ratio
                    valid = False
                    status_type = "wrong_ar"
                    if video_info:
                        current_ar = video_info['aspect_ratio']
                        message = f"Not 9:16 AR (current: {current_ar:.2f}) - Auto-fix available"
                    else:
                        message = "Cannot read video - Invalid format"
                        
                else:
                    # New file - do basic validation
                    try:
                        file_size = os.path.getsize(video_path)
                        if file_size == 0:
                            valid = False
                            status_type = "invalid"
                            message = "Empty file"
                        elif file_size < 1024:  # Less than 1KB
                            valid = False
                            status_type = "invalid"
                            message = "File too small"
                        else:
                            print(f"File size: {file_size} bytes")
                            
                    except Exception as e:
                        valid = False
                        status_type = "invalid"
                        message = f"Cannot read file: {str(e)}"
                
                videos.append({
                    'filename': filename,
                    'path': video_path,
                    'valid': valid,
                    'message': message,
                    'status_type': status_type,  # new, duplicate, retry, invalid, wrong_ar
                    'youtube_url': youtube_url,
                    'upload_date': upload_date,
                    'needs_cropping': needs_cropping,
                    'aspect_ratio': video_info['aspect_ratio'] if video_info else None,
                    'size': os.path.getsize(video_path) if os.path.exists(video_path) else 0
                })
    
    except PermissionError:
        return jsonify({'success': False, 'error': f'Permission denied accessing folder: {folder_path}'})
    except Exception as e:
        return jsonify({'success': False, 'error': f'Error scanning folder: {str(e)}'})
    
    print(f"Scan complete: {len(videos)} video files processed")
    return jsonify({'success': True, 'videos': videos})

@app.route('/api/upload_status')
def get_upload_status():
    """Get current upload status"""
    return jsonify(upload_status)

@app.route('/api/test_youtube', methods=['POST'])
def test_youtube():
    """Test YouTube API connection"""
    try:
        youtube = get_youtube_service()
        if not youtube:
            return jsonify({'success': False, 'error': 'No YouTube credentials'})
        
        # Test API call
        request_obj = youtube.channels().list(
            part='snippet',
            mine=True
        )
        response = request_obj.execute()
        
        channel_name = response['items'][0]['snippet']['title'] if response['items'] else 'Unknown'
        
        return jsonify({
            'success': True,
            'channel_name': channel_name,
            'message': f'Connected to channel: {channel_name}'
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/health')
def health_check():
    """Health check endpoint"""
    config = load_config()
    has_credentials = config.get('youtube_credentials') is not None
    
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'version': '1.0.0',
        'google_api_available': GOOGLE_AVAILABLE,
        'client_secrets_exists': os.path.exists(CLIENT_SECRETS_FILE),
        'youtube_connected': has_credentials,
        'insecure_transport_enabled': os.environ.get('OAUTHLIB_INSECURE_TRANSPORT') == '1'
    })

@app.route('/api/test_upload', methods=['POST'])
def test_upload():
    """Test route"""
    print("Test route called!")
    try:
        data = request.get_json()
        return jsonify({
            'success': True,
            'message': 'Test route working',
            'received_data': data
        })
    except Exception as e:
        print(f"Test route error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/debug/routes')
def debug_routes():
    """Show all registered routes"""
    print("Debug routes called!")
    try:
        routes = []
        for rule in app.url_map.iter_rules():
            routes.append({
                'endpoint': rule.endpoint,
                'methods': list(rule.methods),
                'path': str(rule)
            })
        return jsonify(routes)
    except Exception as e:
        print(f"Debug routes error: {e}")
        return jsonify({'error': str(e)}), 500

def upload_video_to_youtube(video_path, title, description, tags=[], privacy='public'):
    """Upload video to YouTube with progress tracking"""
    try:
        print(f"Starting YouTube upload: {title}")
        
        youtube = get_youtube_service()
        if not youtube:
            return False, "No YouTube service available"
        
        # Prepare video metadata
        body = {
            'snippet': {
                'title': title,
                'description': description,
                'tags': tags,
                'categoryId': '22',  # People & Blogs
                'defaultLanguage': 'en'
            },
            'status': {
                'privacyStatus': privacy,
                'selfDeclaredMadeForKids': False
            }
        }
        
        print(f"Video metadata prepared: {body['snippet']['title']}")
        
        # Create media upload object
        media = MediaFileUpload(
            video_path,
            resumable=True,
            chunksize=1024*1024  # 1MB chunks
        )
        
        print(f"Media upload object created for: {video_path}")
        
        # Create insert request
        insert_request = youtube.videos().insert(
            part=','.join(body.keys()),
            body=body,
            media_body=media
        )
        
        print("Starting upload to YouTube...")
        
        # Execute upload with progress tracking
        response = None
        while response is None:
            try:
                status, response = insert_request.next_chunk()
                if status:
                    progress = int(status.progress() * 100)
                    print(f"Upload progress: {progress}%")
                    
                    # Emit real-time progress
                    socketio.emit('upload_progress', {
                        'progress': progress,
                        'status': 'uploading',
                        'filename': os.path.basename(video_path)
                    })
                    
            except Exception as chunk_error:
                print(f"Chunk upload error: {chunk_error}")
                return False, str(chunk_error)
        
        if response:
            video_id = response['id']
            video_url = f"https://www.youtube.com/watch?v={video_id}"
            print(f"Upload successful! Video ID: {video_id}")
            print(f"Video URL: {video_url}")
            return True, video_url
        else:
            return False, "Upload failed - no response"
            
    except Exception as e:
        print(f"Upload error: {e}")
        import traceback
        traceback.print_exc()
        return False, str(e)

def process_upload_queue():
    """Process videos in upload queue with real YouTube uploads"""
    global upload_queue, upload_status, upload_paused
    
    print(f"Processing upload queue. {len(upload_queue)} items, paused: {upload_paused}")
    
    if upload_paused or not upload_queue:
        print("Upload processing stopped - paused or empty queue")
        return
    
    current_upload = upload_queue[0]
    video_id = current_upload['id']
    
    print(f"Processing upload: {current_upload['filename']}")
    
    try:
        # Update status to uploading
        upload_status[video_id]['status'] = 'uploading'
        upload_status[video_id]['progress'] = 0
        
        print(f"Updated status for {video_id} to uploading")
        
        # Emit status update
        socketio.emit('upload_status_update', upload_status)
        
        # Upload to YouTube
        print(f"Starting YouTube upload for: {current_upload['path']}")
        
        success, result = upload_video_to_youtube(
            current_upload['path'],
            current_upload['title'],
            current_upload['description'],
            current_upload['tags'].split(',') if current_upload['tags'] else [],
            current_upload.get('privacy', 'public')
        )
        
        if success:
            upload_status[video_id]['status'] = 'completed'
            upload_status[video_id]['progress'] = 100
            upload_status[video_id]['youtube_url'] = result
            
            print(f"Upload completed successfully: {result}")
            
            # Add to history
            history = load_history()
            history.append({
                'id': video_id,
                'filename': current_upload['filename'],
                'title': current_upload['title'],
                'upload_date': datetime.now().isoformat(),
                'youtube_url': result,
                'status': 'completed'
            })
            save_history(history)
            
            print(f"Added to history: {current_upload['filename']}")
            
        else:
            upload_status[video_id]['status'] = 'failed'
            upload_status[video_id]['error'] = result
            
            print(f"Upload failed: {result}")
            
            # Add failed upload to history
            history = load_history()
            history.append({
                'id': video_id,
                'filename': current_upload['filename'],
                'title': current_upload['title'],
                'upload_date': datetime.now().isoformat(),
                'youtube_url': None,
                'status': 'failed',
                'error': result
            })
            save_history(history)
            
    except Exception as e:
        upload_status[video_id]['status'] = 'failed'
        upload_status[video_id]['error'] = str(e)
        print(f"Upload processing error: {e}")
        import traceback
        traceback.print_exc()
    
    # Remove from queue
    upload_queue.pop(0)
    print(f"Removed from queue. {len(upload_queue)} items remaining")
    
    # Emit final status
    socketio.emit('upload_status_update', upload_status)
    
    # Process next item with delay
    if upload_queue and not upload_paused:
        config = load_config()
        delay = config.get('upload_preferences', {}).get('upload_delay', 10)
        print(f"Processing next upload in {delay} seconds...")
        threading.Timer(delay, process_upload_queue).start()
    else:
        print("Upload queue processing complete")

@app.route('/api/schedule_upload', methods=['POST'])
def schedule_upload():
    """Schedule video uploads - REAL VERSION"""
    global upload_queue, upload_status
    
    try:
        print("=== REAL UPLOAD STARTED ===")
        data = request.get_json()
        print(f"Received upload request: {data}")
        
        if not data:
            return jsonify({'success': False, 'error': 'No data received'})
            
        videos = data.get('videos', [])
        
        if not videos:
            return jsonify({'success': False, 'error': 'No videos provided'})
        
        print(f"Processing {len(videos)} videos for real upload")
        
        # Check YouTube connection
        config = load_config()
        if not config.get('youtube_credentials'):
            return jsonify({'success': False, 'error': 'YouTube not connected. Go to Settings to connect.'})
        
        # Test YouTube service
        try:
            youtube = get_youtube_service()
            if not youtube:
                return jsonify({'success': False, 'error': 'YouTube service not available'})
            print("YouTube service verified")
        except Exception as e:
            return jsonify({'success': False, 'error': f'YouTube connection error: {str(e)}'})
        
        # Clear existing queue and status
        upload_queue = []
        upload_status = {}
        
        # Add videos to queue
        for video in videos:
            video_id = str(uuid.uuid4())
            
            # Verify file exists
            if not os.path.exists(video['path']):
                print(f"Warning: File not found: {video['path']}")
                continue
            
            print(f"Adding to queue: {video['filename']}")
            
            upload_queue.append({
                'id': video_id,
                'filename': video['filename'],
                'path': video['path'],
                'title': video.get('title', os.path.splitext(video['filename'])[0]),
                'description': video.get('description', f"Uploaded via YouTube Uploader\n\nOriginal filename: {video['filename']}"),
                'tags': video.get('tags', 'shorts,upload,automated'),
                'privacy': video.get('privacy', 'public')
            })
            
            upload_status[video_id] = {
                'filename': video['filename'],
                'status': 'queued',
                'progress': 0,
                'thumbnail': None,
                'youtube_url': None,
                'error': None
            }
        
        queued_count = len(upload_queue)
        print(f"Queued {queued_count} videos for REAL upload")
        
        if queued_count == 0:
            return jsonify({'success': False, 'error': 'No valid videos to upload'})
        
        # Start processing immediately
        print(f"Starting REAL upload of {queued_count} videos...")
        threading.Timer(2.0, process_upload_queue).start()
        
        return jsonify({
            'success': True, 
            'message': f'REAL upload started! {queued_count} videos queued',
            'queued': queued_count
        })
        
    except Exception as e:
        print(f"Error in real schedule_upload: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': f'Server error: {str(e)}'})

# Import the video processor
from video_processor import VideoProcessor

# Global video processor instance
video_processor = VideoProcessor()

@app.route('/api/analyze_video', methods=['POST'])
def analyze_video():
    """Analyze a video for aspect ratio and cropping needs"""
    try:
        data = request.get_json()
        video_path = data.get('video_path')
        
        if not video_path or not os.path.exists(video_path):
            return jsonify({'success': False, 'error': 'Video file not found'})
        
        print(f"Analyzing video: {video_path}")
        
        # Analyze the video
        analysis = video_processor.analyze_video_for_cropping(video_path)
        
        if 'error' in analysis:
            return jsonify({'success': False, 'error': analysis['error']})
        
        return jsonify({
            'success': True,
            'analysis': analysis
        })
        
    except Exception as e:
        print(f"Error analyzing video: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/process_video', methods=['POST'])
def process_video():
    """Process a video with intelligent cropping"""
    try:
        data = request.get_json()
        video_path = data.get('video_path')
        
        if not video_path or not os.path.exists(video_path):
            return jsonify({'success': False, 'error': 'Video file not found'})
        
        print(f"Processing video: {video_path}")
        
        # First analyze the video
        analysis = video_processor.analyze_video_for_cropping(video_path)
        
        if 'error' in analysis:
            return jsonify({'success': False, 'error': analysis['error']})
        
        if not analysis['needs_processing']:
            return jsonify({
                'success': False, 
                'error': 'Video does not need processing - already 9:16 aspect ratio'
            })
        
        # Generate output filename
        base_name = os.path.splitext(video_path)[0]
        extension = os.path.splitext(video_path)[1]
        output_path = f"{base_name}_crop916{extension}"
        
        # Check if output already exists
        if os.path.exists(output_path):
            return jsonify({
                'success': False, 
                'error': f'Processed file already exists: {os.path.basename(output_path)}'
            })
        
        # Process the video
        success = video_processor.process_video(
            video_path, 
            output_path, 
            analysis['crop_params']
        )
        
        if success:
            return jsonify({
                'success': True,
                'output_path': output_path,
                'output_filename': os.path.basename(output_path),
                'analysis': analysis
            })
        else:
            return jsonify({'success': False, 'error': 'Video processing failed'})
        
    except Exception as e:
        print(f"Error processing video: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)})


if __name__ == '__main__':
    # Create necessary directories
    os.makedirs('static/thumbnails', exist_ok=True)
    os.makedirs('logs', exist_ok=True)
    
    # Setup logging
    logging.basicConfig(level=logging.INFO)
    
    print("YouTube Uploader Starting...")
    print(f"Google API Available: {GOOGLE_AVAILABLE}")
    print(f"Client Secrets Exists: {os.path.exists(CLIENT_SECRETS_FILE)}")
    print(f"Insecure Transport Enabled: {os.environ.get('OAUTHLIB_INSECURE_TRANSPORT') == '1'}")
    
    # Run the app
    socketio.run(app, host='127.0.0.1', port=5000, debug=True)
