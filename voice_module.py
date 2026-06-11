#!/usr/bin/env python3
"""
Voice Module - Speech-to-Text and Text-to-Speech
- STT: Faster-Whisper (local, fast)
- TTS: Coqui XTTS v2 with voice cloning (Batman voice)
"""

import os
import sys
import tempfile
import logging
from pathlib import Path
from typing import Optional, Tuple
import hashlib
import json

logger = logging.getLogger(__name__)

# Base directory
BASE_DIR = Path(__file__).parent.resolve()
VOICE_DIR = BASE_DIR / "voices"
VOICE_DIR.mkdir(exist_ok=True)
CACHE_DIR = BASE_DIR / "voice_cache"
CACHE_DIR.mkdir(exist_ok=True)

# Voice sample for cloning (Batman)
BATMAN_VOICE_SAMPLE = VOICE_DIR / "batman_sample.wav"

# Try to import voice libraries
STT_AVAILABLE = False
TTS_AVAILABLE = False

# Speech-to-Text with faster-whisper
try:
    from faster_whisper import WhisperModel
    STT_AVAILABLE = True
except ImportError:
    try:
        import whisper
        STT_AVAILABLE = True
        logger.info("Using openai-whisper (slower than faster-whisper)")
    except ImportError:
        logger.warning("No STT available. Install: pip install faster-whisper")

# Text-to-Speech with Coqui TTS
# Compat shim: coqui-tts still imports `isin_mps_friendly`, a helper that
# transformers >= 5.0 removed from transformers.pytorch_utils. Back-fill it so
# Coqui XTTS imports cleanly without downgrading the agent's transformers stack.
try:
    import torch as _torch
    import transformers.pytorch_utils as _ptu
    if not hasattr(_ptu, "isin_mps_friendly"):
        def _isin_mps_friendly(elements, test_elements):
            if getattr(elements, "device", None) is not None and elements.device.type == "mps":
                test = test_elements if isinstance(test_elements, _torch.Tensor) else _torch.tensor(test_elements, device=elements.device)
                return (elements.unsqueeze(-1) == test.reshape(-1)).any(dim=-1)
            return _torch.isin(elements, test_elements)
        _ptu.isin_mps_friendly = _isin_mps_friendly
except Exception:
    pass

try:
    from TTS.api import TTS
    TTS_AVAILABLE = True
except ImportError:
    logger.warning("Coqui TTS not available. Install: pip install coqui-tts")

# Alternative: Edge TTS (Microsoft, free, has deep voices)
EDGE_TTS_AVAILABLE = False
try:
    import edge_tts
    import asyncio
    EDGE_TTS_AVAILABLE = True
except ImportError:
    pass


class SpeechToText:
    """Convert speech to text using Whisper."""
    
    def __init__(self, model_size: str = "base"):
        """
        Initialize STT engine.
        
        Args:
            model_size: tiny, base, small, medium, large-v2, large-v3
        """
        self.model = None
        self.model_size = model_size
        self.use_faster_whisper = False
        
        try:
            from faster_whisper import WhisperModel
            # Use CPU or CUDA
            device = "cuda" if self._cuda_available() else "cpu"
            compute_type = "float16" if device == "cuda" else "int8"
            
            logger.info(f"Loading faster-whisper {model_size} on {device}...")
            self.model = WhisperModel(model_size, device=device, compute_type=compute_type)
            self.use_faster_whisper = True
            logger.info("✅ Faster-Whisper loaded")
        except ImportError:
            try:
                import whisper
                logger.info(f"Loading openai-whisper {model_size}...")
                self.model = whisper.load_model(model_size)
                logger.info("✅ OpenAI Whisper loaded")
            except ImportError:
                raise RuntimeError("No Whisper implementation available")
    
    def _cuda_available(self) -> bool:
        try:
            import torch
            return torch.cuda.is_available()
        except:
            return False
    
    def transcribe(self, audio_path: str, language: str = "en") -> str:
        """
        Transcribe audio file to text.
        
        Args:
            audio_path: Path to audio file (mp3, wav, ogg, etc.)
            language: Language code (en, de, etc.) or None for auto-detect
            
        Returns:
            Transcribed text
        """
        if not self.model:
            raise RuntimeError("STT model not loaded")
        
        if self.use_faster_whisper:
            segments, info = self.model.transcribe(
                audio_path,
                language=language,
                beam_size=5,
                vad_filter=True
            )
            text = " ".join([segment.text for segment in segments])
        else:
            result = self.model.transcribe(audio_path, language=language)
            text = result["text"]
        
        return text.strip()


class TextToSpeech:
    """Convert text to speech with voice cloning."""
    
    def __init__(self, voice_sample: Optional[str] = None):
        """
        Initialize TTS engine.
        
        Args:
            voice_sample: Path to voice sample for cloning (WAV, 6-30 seconds)
        """
        self.tts = None
        self.voice_sample = voice_sample or str(BATMAN_VOICE_SAMPLE)
        self.use_xtts = False
        self.use_edge = False
        
        # Try Coqui XTTS first (best for voice cloning)
        if TTS_AVAILABLE:
            try:
                logger.info("Loading Coqui XTTS v2...")
                # XTTS v2 supports voice cloning
                self.tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2")
                
                # Move to GPU if available
                if self._cuda_available():
                    self.tts.to("cuda")
                
                self.use_xtts = True
                logger.info("✅ Coqui XTTS v2 loaded (voice cloning enabled)")
            except Exception as e:
                logger.warning(f"XTTS failed: {e}, trying Bark...")
                try:
                    self.tts = TTS("tts_models/en/ljspeech/tacotron2-DDC")
                    logger.info("✅ Tacotron2 loaded (no voice cloning)")
                except Exception as e2:
                    logger.warning(f"Tacotron2 failed: {e2}")
        
        # Fallback to Edge TTS (Microsoft, has deep voices)
        if not self.tts and EDGE_TTS_AVAILABLE:
            self.use_edge = True
            logger.info("✅ Using Edge TTS (deep voice mode)")
    
    def _cuda_available(self) -> bool:
        try:
            import torch
            return torch.cuda.is_available()
        except:
            return False
    
    def _get_cache_path(self, text: str) -> Path:
        """Get cache path for text."""
        text_hash = hashlib.md5(text.encode()).hexdigest()[:16]
        return CACHE_DIR / f"tts_{text_hash}.wav"
    
    def synthesize(self, text: str, output_path: Optional[str] = None, 
                   use_cache: bool = True) -> str:
        """
        Convert text to speech.
        
        Args:
            text: Text to speak
            output_path: Output file path (auto-generated if None)
            use_cache: Use cached audio if available
            
        Returns:
            Path to generated audio file
        """
        # Check cache
        if use_cache:
            cache_path = self._get_cache_path(text)
            if cache_path.exists():
                return str(cache_path)
        
        # Generate output path
        if not output_path:
            output_path = str(CACHE_DIR / f"tts_{os.urandom(8).hex()}.wav")
        
        if self.use_xtts and self.tts:
            return self._synthesize_xtts(text, output_path)
        elif self.use_edge:
            return self._synthesize_edge(text, output_path)
        else:
            raise RuntimeError("No TTS engine available")
    
    def _synthesize_xtts(self, text: str, output_path: str) -> str:
        """Synthesize with XTTS (voice cloning)."""
        # Check if we have a voice sample
        if os.path.exists(self.voice_sample):
            # Use voice cloning
            self.tts.tts_to_file(
                text=text,
                file_path=output_path,
                speaker_wav=self.voice_sample,
                language="en"
            )
        else:
            # Use default voice
            logger.warning(f"No voice sample at {self.voice_sample}, using default")
            self.tts.tts_to_file(
                text=text,
                file_path=output_path,
                language="en"
            )
        
        # Cache it
        cache_path = self._get_cache_path(text)
        if str(output_path) != str(cache_path):
            import shutil
            shutil.copy(output_path, cache_path)
        
        return output_path
    
    def _synthesize_edge(self, text: str, output_path: str) -> str:
        """Synthesize with Edge TTS (deep Batman-like voice)."""
        import asyncio
        import edge_tts
        
        async def _generate():
            # Deep male voices that sound Batman-like:
            # en-US-GuyNeural - Deep American male
            # en-GB-RyanNeural - Deep British male  
            # en-US-ChristopherNeural - Authoritative
            voice = "en-US-GuyNeural"
            
            # Adjust pitch and rate for more Batman feel
            # Rate: -50% to +50%, Pitch: -50Hz to +50Hz
            communicate = edge_tts.Communicate(
                text, 
                voice,
                rate="-10%",  # Slightly slower
                pitch="-20Hz"  # Deeper pitch
            )
            
            await communicate.save(output_path)
        
        # Handle running from async context
        try:
            loop = asyncio.get_running_loop()
            # We're inside an async context, create a new thread
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                pool.submit(asyncio.run, _generate()).result()
        except RuntimeError:
            # No running loop, safe to use asyncio.run
            asyncio.run(_generate())
        
        # Cache it
        cache_path = self._get_cache_path(text)
        if str(output_path) != str(cache_path):
            import shutil
            shutil.copy(output_path, cache_path)
        
        return output_path


class VoiceManager:
    """Unified voice interface for STT and TTS."""
    
    def __init__(self, 
                 stt_model: str = "base",
                 voice_sample: Optional[str] = None,
                 enable_voice_tasks: list = None):
        """
        Initialize voice manager.
        
        Args:
            stt_model: Whisper model size
            voice_sample: Path to voice sample for cloning
            enable_voice_tasks: List of task types to respond with voice
                               ["all", "coding", "chat", "summary", etc.]
        """
        self.stt = None
        self.tts = None
        self.voice_tasks = enable_voice_tasks or ["chat", "summary", "creative"]
        
        # Initialize STT
        if STT_AVAILABLE:
            try:
                self.stt = SpeechToText(stt_model)
            except Exception as e:
                logger.error(f"STT init failed: {e}")
        
        # Initialize TTS
        if TTS_AVAILABLE or EDGE_TTS_AVAILABLE:
            try:
                self.tts = TextToSpeech(voice_sample)
            except Exception as e:
                logger.error(f"TTS init failed: {e}")
    
    def transcribe(self, audio_path: str) -> str:
        """Convert speech to text."""
        if not self.stt:
            raise RuntimeError("STT not available")
        return self.stt.transcribe(audio_path)
    
    def speak(self, text: str, output_path: Optional[str] = None) -> str:
        """Convert text to speech."""
        if not self.tts:
            raise RuntimeError("TTS not available")
        return self.tts.synthesize(text, output_path)
    
    def should_respond_with_voice(self, task_type: str) -> bool:
        """Check if we should respond with voice for this task type."""
        if "all" in self.voice_tasks:
            return True
        return task_type.lower() in [t.lower() for t in self.voice_tasks]
    
    def get_status(self) -> dict:
        """Get voice module status."""
        return {
            "stt_available": self.stt is not None,
            "stt_model": self.stt.model_size if self.stt else None,
            "tts_available": self.tts is not None,
            "tts_engine": "xtts" if (self.tts and self.tts.use_xtts) else 
                         ("edge" if (self.tts and self.tts.use_edge) else None),
            "voice_cloning": self.tts.use_xtts if self.tts else False,
            "voice_sample": os.path.exists(self.tts.voice_sample) if self.tts else False,
            "voice_tasks": self.voice_tasks
        }


# Singleton instance
_voice_manager: Optional[VoiceManager] = None

def get_voice_manager(stt_model: str = "base", 
                      voice_sample: Optional[str] = None) -> VoiceManager:
    """Get or create voice manager singleton."""
    global _voice_manager
    if _voice_manager is None:
        _voice_manager = VoiceManager(stt_model, voice_sample)
    return _voice_manager


def download_batman_voice_sample():
    """
    Instructions for getting a Batman voice sample.
    For voice cloning, you need a 6-30 second WAV file of Batman speaking.
    """
    print("""
🦇 BATMAN VOICE SETUP
═══════════════════════════════════════════════════════════════

To enable Batman voice cloning, you need a voice sample:

1. OPTION A - Use Kevin Conroy Batman clips:
   - Search YouTube for "Kevin Conroy Batman voice"
   - Download a 10-30 second clip of clear speech
   - Convert to WAV: ffmpeg -i clip.mp4 -ar 22050 batman_sample.wav
   - Place in: {voice_dir}/batman_sample.wav

2. OPTION B - Use Christian Bale Batman:
   - Search for "Dark Knight Batman quotes"
   - Same process as above

3. OPTION C - Record yourself doing Batman voice:
   - Record 15-30 seconds speaking clearly
   - Save as WAV at 22050 Hz

Voice sample requirements:
- Format: WAV (mono, 22050 Hz preferred)
- Length: 6-30 seconds
- Content: Clear speech, minimal background noise
- Quality: Good recording quality

Place the file at:
  {voice_path}

═══════════════════════════════════════════════════════════════
""".format(voice_dir=VOICE_DIR, voice_path=BATMAN_VOICE_SAMPLE))


if __name__ == "__main__":
    print("🎤 Voice Module Status")
    print("=" * 50)
    print(f"STT Available: {STT_AVAILABLE}")
    print(f"TTS Available: {TTS_AVAILABLE}")
    print(f"Edge TTS Available: {EDGE_TTS_AVAILABLE}")
    print(f"Voice directory: {VOICE_DIR}")
    print(f"Batman sample exists: {BATMAN_VOICE_SAMPLE.exists()}")
    print()
    
    if not BATMAN_VOICE_SAMPLE.exists():
        download_batman_voice_sample()
    
    # Test if we can initialize
    try:
        vm = get_voice_manager()
        status = vm.get_status()
        print("\n🔧 Voice Manager Status:")
        for k, v in status.items():
            print(f"  {k}: {v}")
    except Exception as e:
        print(f"\n❌ Voice Manager Error: {e}")
        print("\nInstall dependencies:")
        print("  pip install faster-whisper TTS edge-tts")
