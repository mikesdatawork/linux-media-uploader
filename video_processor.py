#!/usr/bin/env python3
"""
Video Processing Module for YouTube Uploader (Audio Fixed)
Handles aspect ratio detection, subject detection, and intelligent cropping
"""

import cv2
import mediapipe as mp
import ffmpeg
import os
import json
import numpy as np
from typing import Tuple, Optional, List, Dict

class VideoProcessor:
    def __init__(self):
        # Initialize MediaPipe
        self.mp_pose = mp.solutions.pose
        self.mp_face = mp.solutions.face_detection
        self.mp_hands = mp.solutions.hands
        
        # Initialize pose detection
        self.pose = self.mp_pose.Pose(
            static_image_mode=False,
            model_complexity=1,
            enable_segmentation=False,
            min_detection_confidence=0.5
        )
        
        # Initialize face detection
        self.face_detection = self.mp_face.FaceDetection(
            model_selection=0,
            min_detection_confidence=0.5
        )
    
    def get_video_info(self, video_path: str) -> Optional[Dict]:
        """Get video information using ffprobe"""
        try:
            probe = ffmpeg.probe(video_path)
            video_stream = next((stream for stream in probe['streams'] if stream['codec_type'] == 'video'), None)
            audio_stream = next((stream for stream in probe['streams'] if stream['codec_type'] == 'audio'), None)
            
            if not video_stream:
                return None
            
            width = int(video_stream['width'])
            height = int(video_stream['height'])
            duration = float(video_stream.get('duration', 0))
            fps = eval(video_stream.get('avg_frame_rate', '30/1'))
            bitrate = int(video_stream.get('bit_rate', 0))
            
            return {
                'width': width,
                'height': height,
                'duration': duration,
                'fps': fps,
                'bitrate': bitrate,
                'aspect_ratio': width / height,
                'target_ratio': 9/16,  # YouTube Shorts
                'has_audio': audio_stream is not None
            }
        except Exception as e:
            print(f"Error getting video info: {e}")
            return None
    
    def detect_subjects(self, frame: np.ndarray) -> Tuple[int, int]:
        """Detect subjects in frame and return center point"""
        height, width = frame.shape[:2]
        subjects = []
        
        # Convert BGR to RGB for MediaPipe
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        
        # Detect faces
        try:
            face_results = self.face_detection.process(rgb_frame)
            if face_results.detections:
                for detection in face_results.detections:
                    bbox = detection.location_data.relative_bounding_box
                    center_x = int((bbox.xmin + bbox.width/2) * width)
                    center_y = int((bbox.ymin + bbox.height/2) * height)
                    subjects.append((center_x, center_y, 'face'))
        except Exception as e:
            print(f"Face detection error: {e}")
        
        # Detect pose (people)
        try:
            pose_results = self.pose.process(rgb_frame)
            if pose_results.pose_landmarks:
                # Get torso center from pose landmarks
                landmarks = pose_results.pose_landmarks.landmark
                
                # Use shoulder and hip landmarks to find torso center
                left_shoulder = landmarks[self.mp_pose.PoseLandmark.LEFT_SHOULDER.value]
                right_shoulder = landmarks[self.mp_pose.PoseLandmark.RIGHT_SHOULDER.value]
                left_hip = landmarks[self.mp_pose.PoseLandmark.LEFT_HIP.value]
                right_hip = landmarks[self.mp_pose.PoseLandmark.RIGHT_HIP.value]
                
                center_x = int(((left_shoulder.x + right_shoulder.x + left_hip.x + right_hip.x) / 4) * width)
                center_y = int(((left_shoulder.y + right_shoulder.y + left_hip.y + right_hip.y) / 4) * height)
                subjects.append((center_x, center_y, 'person'))
        except Exception as e:
            print(f"Pose detection error: {e}")
        
        if subjects:
            # If multiple subjects, find center of mass
            avg_x = sum(s[0] for s in subjects) / len(subjects)
            avg_y = sum(s[1] for s in subjects) / len(subjects)
            return int(avg_x), int(avg_y)
        else:
            # No subjects detected, return center of frame
            return width // 2, height // 2
    
    def calculate_crop_parameters(self, video_info: Dict, subject_center: Tuple[int, int]) -> Dict:
        """Calculate optimal crop parameters for 9:16 aspect ratio"""
        width = video_info['width']
        height = video_info['height']
        target_ratio = 9/16  # 0.5625
        current_ratio = width / height
        
        subject_x, subject_y = subject_center
        
        if abs(current_ratio - target_ratio) < 0.01:
            # Already correct aspect ratio
            return {
                'needs_crop': False,
                'crop_x': 0,
                'crop_y': 0,
                'crop_width': width,
                'crop_height': height
            }
        
        if current_ratio > target_ratio:
            # Video is too wide - need to crop width
            target_width = int(height * target_ratio)
            
            # Center crop around subject, but keep within bounds
            crop_x = max(0, min(subject_x - target_width//2, width - target_width))
            crop_y = 0
            
            return {
                'needs_crop': True,
                'crop_x': crop_x,
                'crop_y': crop_y,
                'crop_width': target_width,
                'crop_height': height,
                'operation': 'crop_width'
            }
        else:
            # Video is too narrow - need to crop height or add padding
            target_height = int(width / target_ratio)
            
            if target_height <= height:
                # Can crop to correct ratio
                crop_y = max(0, min(subject_y - target_height//2, height - target_height))
                crop_x = 0
                
                return {
                    'needs_crop': True,
                    'crop_x': crop_x,
                    'crop_y': crop_y,
                    'crop_width': width,
                    'crop_height': target_height,
                    'operation': 'crop_height'
                }
            else:
                # Need to add padding (black bars)
                pad_height = target_height - height
                
                return {
                    'needs_crop': True,
                    'crop_x': 0,
                    'crop_y': 0,
                    'crop_width': width,
                    'crop_height': height,
                    'pad_height': pad_height,
                    'operation': 'add_padding'
                }
    
    def analyze_video_for_cropping(self, video_path: str) -> Dict:
        """Analyze video to determine if cropping is needed and calculate parameters"""
        print(f"Analyzing video: {video_path}")
        
        # Get video information
        video_info = self.get_video_info(video_path)
        if not video_info:
            return {'error': 'Could not read video information'}
        
        print(f"Video dimensions: {video_info['width']}x{video_info['height']}")
        print(f"Current aspect ratio: {video_info['aspect_ratio']:.4f}")
        print(f"Target aspect ratio: {video_info['target_ratio']:.4f}")
        print(f"Has audio: {video_info['has_audio']}")
        
        # Check if already correct aspect ratio
        if abs(video_info['aspect_ratio'] - video_info['target_ratio']) < 0.01:
            return {
                'needs_processing': False,
                'reason': 'Already 9:16 aspect ratio',
                'video_info': video_info
            }
        
        # Sample a few frames to detect subjects
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return {'error': 'Could not open video file'}
        
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        sample_frames = min(5, total_frames)  # Sample up to 5 frames
        
        subject_centers = []
        
        for i in range(sample_frames):
            frame_num = int((i / sample_frames) * total_frames)
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_num)
            
            ret, frame = cap.read()
            if ret:
                center_x, center_y = self.detect_subjects(frame)
                subject_centers.append((center_x, center_y))
                print(f"Frame {frame_num}: Subject center at ({center_x}, {center_y})")
        
        cap.release()
        
        if subject_centers:
            # Average the subject centers
            avg_x = sum(c[0] for c in subject_centers) / len(subject_centers)
            avg_y = sum(c[1] for c in subject_centers) / len(subject_centers)
            subject_center = (int(avg_x), int(avg_y))
        else:
            # Fallback to center
            subject_center = (video_info['width'] // 2, video_info['height'] // 2)
        
        print(f"Average subject center: {subject_center}")
        
        # Calculate crop parameters
        crop_params = self.calculate_crop_parameters(video_info, subject_center)
        
        return {
            'needs_processing': crop_params['needs_crop'],
            'video_info': video_info,
            'subject_center': subject_center,
            'crop_params': crop_params
        }
    
    def process_video(self, input_path: str, output_path: str, crop_params: Dict, progress_callback=None) -> bool:
        """Process video with intelligent cropping - AUDIO FIXED VERSION"""
        try:
            print(f"Processing video: {input_path} -> {output_path}")
            
            # Get video info
            video_info = self.get_video_info(input_path)
            if not video_info:
                print("Could not get video info")
                return False
            
            print(f"Video has audio: {video_info['has_audio']}")
            
            # Start with input
            input_stream = ffmpeg.input(input_path)
            
            # Apply crop or pad filter based on operation
            if crop_params['operation'] == 'crop_width' or crop_params['operation'] == 'crop_height':
                # Crop operation
                video_stream = input_stream.video.crop(
                    crop_params['crop_x'],
                    crop_params['crop_y'], 
                    crop_params['crop_width'],
                    crop_params['crop_height']
                )
                print(f"Applied crop: {crop_params['crop_width']}x{crop_params['crop_height']} at ({crop_params['crop_x']},{crop_params['crop_y']})")
                
            elif crop_params['operation'] == 'add_padding':
                # Pad operation  
                total_height = crop_params['crop_height'] + crop_params['pad_height']
                pad_y = crop_params['pad_height'] // 2
                video_stream = input_stream.video.filter('pad', 
                    crop_params['crop_width'], 
                    total_height, 
                    0, 
                    pad_y, 
                    'black'
                )
                print(f"Applied padding: {crop_params['crop_width']}x{total_height} with {crop_params['pad_height']} padding")
                
            else:
                video_stream = input_stream.video
            
            # Handle audio conditionally
            output_args = {
                'vcodec': 'libx264',
                'preset': 'medium',
                'crf': '23'
            }
            
            if video_info['has_audio']:
                # Include audio
                audio_stream = input_stream.audio
                output_args['acodec'] = 'aac'
                output = ffmpeg.output(video_stream, audio_stream, output_path, **output_args)
                print("Processing with audio")
            else:
                # No audio - video only
                output = ffmpeg.output(video_stream, output_path, **output_args)
                print("Processing without audio (video-only)")
            
            # Run FFmpeg
            print("Running FFmpeg...")
            ffmpeg.run(output, overwrite_output=True, quiet=False)
            
            print(f"✅ Video processed successfully: {output_path}")
            return True
            
        except ffmpeg.Error as e:
            print(f"❌ FFmpeg error: {e}")
            if e.stderr:
                print(f"FFmpeg stderr: {e.stderr.decode()}")
            return False
        except Exception as e:
            print(f"❌ Error processing video: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def cleanup(self):
        """Clean up MediaPipe resources"""
        if hasattr(self, 'pose'):
            self.pose.close()
        if hasattr(self, 'face_detection'):
            self.face_detection.close()
