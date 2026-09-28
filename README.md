unlock_guard.py
A second layer of security for a Windows laptop. After unlock it asks for a secret code. Wrong attempts capture a webcam photo, and too many failures shut the laptop down.

**Note:** this is a deterrent, not military-grade security.

## Installation

    pip install -r requirements.txt

## Usage

    python hand_body_detection.py
    python advanced_tracker.py
    python unlock_guard.py --setup   # first time only
    python unlock_guard.py

## Requirements
- Python 3.8+ (tested on 3.13)
- A webcam
- Windows for volume control and unlock guard

## Controls
See the comments at the top of each script for keyboard controls.
