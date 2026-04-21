"""Screen recorder using macOS native screencapture.

Records the full screen as .mp4 video. For Linux, use ffmpeg instead.
"""

import os
import signal
import subprocess
import sys
import time
from datetime import datetime


class ScreenRecorder:
    """Records the screen as an .mp4 video file."""

    def __init__(self, output_path: str):
        self.output_path = output_path
        self.proc = None

    def start(self) -> datetime:
        """Start screen recording.

        Returns:
            datetime of actual recording start (right after subprocess launches).
        """
        if sys.platform == "darwin":
            # macOS native screen recording
            self.proc = subprocess.Popen(
                ["screencapture", "-v", self.output_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            # Linux: use ffmpeg with x11grab
            self.proc = subprocess.Popen(
                [
                    "ffmpeg", "-y",
                    "-f", "x11grab",
                    "-framerate", "30",
                    "-i", ":0",
                    "-c:v", "libx264",
                    "-preset", "ultrafast",
                    self.output_path,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.PIPE,
            )
        start_time = datetime.now()
        print(f"[ScreenRecorder] Recording started → {self.output_path}")
        return start_time

    def stop(self):
        """Stop screen recording gracefully."""
        if self.proc is None:
            return

        if sys.platform == "darwin":
            # Send SIGINT to gracefully stop macOS screencapture
            os.kill(self.proc.pid, signal.SIGINT)
        else:
            # For ffmpeg, send 'q' to stdin
            if self.proc.stdin:
                self.proc.stdin.write(b"q")
                self.proc.stdin.flush()

        # Wait for the process to finish writing the file
        timeout = 10
        start = time.time()
        while self.proc.poll() is None:
            if time.time() - start > timeout:
                self.proc.kill()
                break
            time.sleep(0.1)

        print(f"[ScreenRecorder] Recording stopped.")
        self.proc = None

    def is_recording(self) -> bool:
        return self.proc is not None and self.proc.poll() is None
