#!/usr/bin/env python3
"""Optional Microsoft Edge TTS backend for Calibre's Read Aloud UI."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import threading
import time
from collections import deque
from importlib.util import find_spec


ENGINE_NAME = "edge"
VOICE_CACHE_MAX_AGE = 7 * 24 * 60 * 60


def _log(message):
    from calibre import prints

    prints("[EAF/ebook-viewer] Edge TTS: " + message)


def edge_tts_available():
    """Return whether the optional Python dependency is installed."""
    return find_spec("edge_tts") is not None


def _percentage(value):
    return "{:+d}%".format(round(max(-1.0, min(float(value), 1.0)) * 100))


def _pitch(value):
    return "{:+d}Hz".format(round(max(-1.0, min(float(value), 1.0)) * 100))


def _voice_cache_path():
    from calibre.constants import cache_dir

    return os.path.join(cache_dir(), "edge-tts-voices.json")


def _read_voice_cache(allow_stale=False):
    path = _voice_cache_path()
    try:
        if not allow_stale and time.time() - os.path.getmtime(path) > VOICE_CACHE_MAX_AGE:
            return None
        with open(path, "r", encoding="utf-8") as cache:
            voices = json.load(cache)
        return voices if isinstance(voices, list) else None
    except (OSError, TypeError, ValueError):
        return None


def _write_voice_cache(voices):
    path = _voice_cache_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temporary = path + ".new"
    with open(temporary, "w", encoding="utf-8") as cache:
        json.dump(voices, cache, ensure_ascii=False)
    os.replace(temporary, path)


def _download_voices():
    import edge_tts

    async def fetch():
        return await asyncio.wait_for(edge_tts.list_voices(), timeout=15)

    return asyncio.run(fetch())


def _calibre_voices():
    from calibre.utils.localization import canonicalize_lang
    from qt.core import QVoice
    from calibre.gui2.tts.types import Voice

    voices = _read_voice_cache()
    if voices is None:
        try:
            voices = _download_voices()
            _write_voice_cache(voices)
        except Exception:
            voices = _read_voice_cache(allow_stale=True)
    if not voices:
        from edge_tts.constants import DEFAULT_VOICE

        locale = DEFAULT_VOICE.partition("-")[0] + "-" + DEFAULT_VOICE.split("-")[1]
        voices = [{
            "ShortName": DEFAULT_VOICE,
            "Locale": locale,
            "Gender": "Unknown",
            "FriendlyName": DEFAULT_VOICE,
            "VoiceTag": {},
        }]

    result = []
    gender_map = {
        "Female": QVoice.Gender.Female,
        "Male": QVoice.Gender.Male,
    }
    for item in voices:
        name = item.get("ShortName") or item.get("Name") or ""
        locale = item.get("Locale") or "und"
        if not name:
            continue
        parts = locale.split("-")
        language = canonicalize_lang(parts[0]) or parts[0]
        country = parts[-1].upper() if len(parts) > 1 else ""
        display_name = name
        prefix = locale + "-"
        if display_name.startswith(prefix):
            display_name = display_name[len(prefix):]
        tags = item.get("VoiceTag") or {}
        details = list(tags.get("ContentCategories") or ())
        details.extend(tags.get("VoicePersonalities") or ())
        result.append(Voice(
            name=name,
            language_code=language,
            country_code=country,
            human_name=display_name,
            notes=", ".join(dict.fromkeys(details)),
            gender=gender_map.get(item.get("Gender"), QVoice.Gender.Unknown),
            engine_data={"locale": locale},
        ))
    return tuple(sorted(result, key=lambda voice: voice.sort_key()))


def _word_boundaries(text, metadata):
    """Map Edge audio timestamps to Calibre character offsets."""
    result = []
    search_from = 0
    for item in metadata:
        word = item.get("text") or ""
        if not word:
            continue
        offset = text.find(word, search_from)
        if offset < 0:
            offset = text.lower().find(word.lower(), search_from)
        if offset < 0:
            continue
        result.append((int(item.get("offset", 0) / 10_000), offset, len(word)))
        search_from = offset + len(word)
    return result


def _synthesize(text, voice, rate, pitch):
    import edge_tts

    audio = []
    metadata = []
    communicate = edge_tts.Communicate(
        text,
        voice=voice,
        rate=_percentage(rate),
        pitch=_pitch(pitch),
        boundary="WordBoundary",
    )
    for item in communicate.stream_sync():
        if item["type"] == "audio":
            audio.append(item["data"])
        elif item["type"] == "WordBoundary":
            metadata.append(item)
    if not audio:
        raise RuntimeError("Microsoft Edge TTS returned no audio")
    return b"".join(audio), _word_boundaries(text, metadata)


def _split_utterances(text, voice):
    """Split a Calibre utterance into short, offset-preserving sentences."""
    from calibre.spell.break_iterator import split_into_sentences_for_tts

    language = (voice or "en").partition("-")[0] or "en"
    utterances = deque(split_into_sentences_for_tts(
        text,
        lang=language,
        min_sentence_length=48,
        max_sentence_length=700,
    ))
    if not utterances and text:
        utterances.append((0, text))
    return utterances


def _edge_backend_class():
    from qt.core import (
        QAudioOutput,
        QMediaDevices,
        QMediaPlayer,
        QObject,
        QTimer,
        QTextToSpeech,
        Qt,
        QUrl,
        pyqtSignal,
    )
    from calibre.gui2.tts.types import EngineSpecificSettings, TTSBackend

    class EdgeTTSBackend(TTSBackend):
        engine_name = ENGINE_NAME
        _synthesis_finished = pyqtSignal(int, int, object, object, str)

        def __init__(self, engine_name="", parent: QObject | None = None):
            del engine_name
            super().__init__(parent)
            self._voices = None
            self._last_error = ""
            self._generation = 0
            self._synthesizing = False
            self._pause_when_ready = False
            self._utterances = deque()
            self._ready_segment = None
            self._audio_path = ""
            self._boundaries = []
            self._boundary_index = 0
            self._was_playing = False
            self._segment_finish_pending = False
            self._state = QTextToSpeech.State.Ready
            self.audio_output = QAudioOutput(self)
            self.player = QMediaPlayer(self)
            self.player.setAudioOutput(self.audio_output)
            self.player.positionChanged.connect(self._position_changed)
            self.player.playbackStateChanged.connect(self._playback_state_changed)
            self.player.mediaStatusChanged.connect(self._media_status_changed)
            self.player.errorOccurred.connect(self._player_error)
            self._synthesis_finished.connect(
                self._synthesis_complete,
                type=Qt.ConnectionType.QueuedConnection,
            )
            self._load_settings()

        @property
        def available_voices(self):
            if self._voices is None:
                self._voices = _calibre_voices()
            return {"": self._voices}

        @property
        def default_output_module(self):
            return ""

        def _load_settings(self):
            self.settings = EngineSpecificSettings.create_from_config(ENGINE_NAME)
            self.audio_output.setVolume(
                1.0 if self.settings.volume is None else self.settings.volume
            )
            if self.settings.audio_device_id:
                for device in QMediaDevices.audioOutputs():
                    if bytes(device.id()) == self.settings.audio_device_id.id:
                        self.audio_output.setDevice(device)
                        break

        def _set_state(self, state):
            if self._state is not state:
                self._state = state
                self.state_changed.emit(state)

        def _remove_audio_file(self):
            path, self._audio_path = self._audio_path, ""
            if path:
                try:
                    os.remove(path)
                except OSError:
                    pass

        def _clear_playback(self):
            self._was_playing = False
            self._segment_finish_pending = False
            self.player.stop()
            self._remove_audio_file()
            self._boundaries = []
            self._boundary_index = 0

        def say(self, text):
            self._generation += 1
            token = self._generation
            self._clear_playback()
            # A previous network request cannot be cancelled reliably.  Its
            # generation token will discard the result, while this flag lets
            # the replacement utterance start immediately.
            self._synthesizing = False
            self._last_error = ""
            self._pause_when_ready = False
            self._ready_segment = None
            voice = self.settings.voice_name
            if not voice:
                from edge_tts.constants import DEFAULT_VOICE
                voice = DEFAULT_VOICE
            rate = self.settings.rate
            pitch = self.settings.pitch
            self._utterances = _split_utterances(text, voice)
            self._synthesis_options = voice, rate, pitch
            self._start_next_synthesis(token)

        def _start_next_synthesis(self, token=None):
            if (
                self._synthesizing
                or self._ready_segment is not None
                or not self._utterances
            ):
                return
            token = self._generation if token is None else token
            offset, text = self._utterances.popleft()
            voice, rate, pitch = self._synthesis_options
            self._synthesizing = True
            def worker():
                try:
                    audio, boundaries = _synthesize(text, voice, rate, pitch)
                    error = ""
                except Exception as exception:
                    audio, boundaries = b"", []
                    error = str(exception) or exception.__class__.__name__
                self._synthesis_finished.emit(
                    token, offset, audio, boundaries, error
                )

            threading.Thread(
                target=worker,
                name="eaf-edge-tts",
                daemon=True,
            ).start()

        def _synthesis_complete(self, token, offset, audio, boundaries, error):
            if token != self._generation:
                return
            self._synthesizing = False
            if error:
                _log("synthesis failed: " + error)
                self._finish_with_error(error)
                return
            self._ready_segment = offset, audio, boundaries
            if not self._audio_path:
                self._play_ready_segment()

        def _play_ready_segment(self):
            if self._ready_segment is None or self._audio_path:
                return
            offset, audio, boundaries = self._ready_segment
            self._ready_segment = None
            handle, path = tempfile.mkstemp(prefix="eaf-edge-tts-", suffix=".mp3")
            try:
                with os.fdopen(handle, "wb") as output:
                    output.write(audio)
            except Exception as exception:
                try:
                    os.close(handle)
                except OSError:
                    pass
                try:
                    os.remove(path)
                except OSError:
                    pass
                self._finish_with_error(
                    str(exception) or exception.__class__.__name__
                )
                return
            self._audio_path = path
            self._boundaries = [
                (at, offset + start, length)
                for at, start, length in boundaries
            ]
            self._boundary_index = 0
            self.player.setSource(QUrl.fromLocalFile(path))
            # Prepare the following sentence before playback starts.  Edge is
            # normally faster than speech, so this keeps sentence boundaries
            # seamless without waiting for the whole Calibre utterance.
            self._start_next_synthesis()
            if not self._pause_when_ready:
                self.player.play()

        def pause(self):
            if (
                self._audio_path
                and self.player.playbackState()
                is QMediaPlayer.PlaybackState.PlayingState
            ):
                self.player.pause()
            elif self._synthesizing:
                self._pause_when_ready = True
                self._set_state(QTextToSpeech.State.Paused)
            else:
                self.player.pause()

        def resume(self):
            self._pause_when_ready = False
            if self._audio_path:
                self.player.play()
            elif self._ready_segment is not None:
                self._play_ready_segment()

        def stop(self):
            self._generation += 1
            self._synthesizing = False
            self._pause_when_ready = False
            self._utterances.clear()
            self._ready_segment = None
            self._clear_playback()
            self._set_state(QTextToSpeech.State.Ready)

        def reload_after_configure(self):
            self.stop()
            self._load_settings()

        def error_message(self):
            return self._last_error

        def _position_changed(self, position):
            while self._boundary_index < len(self._boundaries):
                at, offset, length = self._boundaries[self._boundary_index]
                if at > position:
                    break
                self.saying.emit(offset, length)
                self._boundary_index += 1

        def _playback_state_changed(self, state):
            if state is QMediaPlayer.PlaybackState.PlayingState:
                self._was_playing = True
                if self._pause_when_ready:
                    self.player.pause()
                else:
                    self._set_state(QTextToSpeech.State.Speaking)
            elif state is QMediaPlayer.PlaybackState.PausedState:
                self._set_state(QTextToSpeech.State.Paused)
            elif (
                state is QMediaPlayer.PlaybackState.StoppedState
                and self._was_playing
            ):
                # Some Qt multimedia backends do not emit EndOfMedia for an
                # MP3 supplied by Edge. Defer cleanup until the signal returns.
                self._was_playing = False
                self._schedule_segment_finished()

        def _schedule_segment_finished(self):
            if not self._segment_finish_pending:
                self._segment_finish_pending = True
                QTimer.singleShot(0, self._playback_finished)

        def _playback_finished(self):
            self._segment_finish_pending = False
            if self._audio_path:
                self._was_playing = False
                self._remove_audio_file()
                self._boundaries = []
                self._boundary_index = 0
                if self._ready_segment is not None:
                    self._play_ready_segment()
                elif self._synthesizing or self._utterances:
                    self._start_next_synthesis()
                else:
                    self._set_state(QTextToSpeech.State.Ready)

        def _finish_with_error(self, message):
            self._generation += 1
            self._last_error = message
            self._utterances.clear()
            self._ready_segment = None
            self._synthesizing = False
            self._clear_playback()
            self._set_state(QTextToSpeech.State.Error)

        def _media_status_changed(self, status):
            if status is QMediaPlayer.MediaStatus.EndOfMedia:
                self._schedule_segment_finished()
            elif status is QMediaPlayer.MediaStatus.InvalidMedia:
                self._finish_with_error(
                    self.player.errorString() or "Invalid Edge TTS audio"
                )

        def _player_error(self, error, message):
            if error is QMediaPlayer.Error.NoError:
                return
            error_message = message or self.player.errorString()
            _log("playback failed: " + error_message)
            self._finish_with_error(error_message)

    return EdgeTTSBackend


def install_edge_tts_backend():
    """Register Edge TTS with Calibre without modifying vendored source."""
    if not edge_tts_available():
        return False
    from calibre.gui2.tts import types as tts_types

    if getattr(tts_types, "_eaf_edge_tts_installed", False):
        return True
    original_available_engines = tts_types.available_engines
    original_create_tts_backend = tts_types.create_tts_backend
    backend_class = _edge_backend_class()

    def available_engines():
        engines = dict(original_available_engines())
        engines[ENGINE_NAME] = tts_types.EngineMetadata(
            ENGINE_NAME,
            "Microsoft Edge Online TTS",
            "Online neural voices from Microsoft Edge with word tracking.",
            tts_types.TrackingCapability.WordByWord,
            allows_choosing_audio_device=True,
        )
        return engines

    def create_tts_backend(force_engine=None, config_name=tts_types.CONFIG_NAME):
        configured = force_engine
        if configured is None:
            configured = tts_types.load_config(config_name).get("engine", "")
        if configured == ENGINE_NAME:
            if ENGINE_NAME not in tts_types.engine_instances:
                from qt.core import QApplication
                tts_types.engine_instances[ENGINE_NAME] = backend_class(
                    ENGINE_NAME, QApplication.instance()
                )
            return tts_types.engine_instances[ENGINE_NAME]
        return original_create_tts_backend(force_engine, config_name)

    tts_types.available_engines = available_engines
    tts_types.create_tts_backend = create_tts_backend
    tts_types._eaf_edge_tts_installed = True
    return True
