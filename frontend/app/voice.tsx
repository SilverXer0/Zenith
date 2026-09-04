"use client";

import { useRef, useState } from "react";
import { api, apiBlob } from "../lib/api";

type VoiceInputProps = {
  enabled: boolean;
  onTranscript: (text: string) => void;
  onError: (message: string) => void;
};

export function VoiceInputButton({ enabled, onTranscript, onError }: VoiceInputProps) {
  const [recording, setRecording] = useState(false);
  const [busy, setBusy] = useState(false);
  const recorder = useRef<MediaRecorder | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const chunks = useRef<Blob[]>([]);

  async function transcribe(blob: Blob) {
    setBusy(true);
    try {
      const result = await api<{ text: string }>("/api/voice/transcribe", {
        method: "POST",
        headers: { "Content-Type": blob.type || "audio/webm" },
        body: blob,
      });
      onTranscript(result.text);
    } catch (caught) {
      onError(caught instanceof Error ? caught.message : "Voice input could not be transcribed.");
    } finally { setBusy(false); }
  }

  async function start() {
    if (!enabled || recording || busy) return;
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      onError("This browser does not support microphone recording.");
      return;
    }
    try {
      const nextStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      stream.current = nextStream;
      const mimeType = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4"].find((candidate) => MediaRecorder.isTypeSupported(candidate));
      const nextRecorder = mimeType ? new MediaRecorder(nextStream, { mimeType }) : new MediaRecorder(nextStream);
      chunks.current = [];
      nextRecorder.ondataavailable = (event) => { if (event.data.size) chunks.current.push(event.data); };
      nextRecorder.onstop = () => {
        const blob = new Blob(chunks.current, { type: nextRecorder.mimeType || "audio/webm" });
        chunks.current = [];
        stream.current?.getTracks().forEach((track) => track.stop());
        stream.current = null;
        recorder.current = null;
        setRecording(false);
        if (blob.size) void transcribe(blob);
      };
      recorder.current = nextRecorder;
      nextRecorder.start();
      setRecording(true);
    } catch (caught) {
      stream.current?.getTracks().forEach((track) => track.stop());
      stream.current = null;
      onError(caught instanceof Error ? caught.message : "Microphone access was unavailable.");
    }
  }

  function stop() {
    if (recorder.current?.state === "recording") recorder.current.stop();
  }

  if (!enabled) return <span className="muted text-xs">Voice input not configured</span>;
  return <button className="quiet-button whitespace-nowrap" type="button" onClick={() => (recording ? stop() : void start())} disabled={busy}>{busy ? "Transcribing…" : recording ? "Stop recording" : "Use microphone"}</button>;
}

export function SpeakButton({ enabled, text, onError }: { enabled: boolean; text: string; onError: (message: string) => void }) {
  const [playing, setPlaying] = useState(false);

  async function speak() {
    if (!enabled || playing) return;
    setPlaying(true);
    let url = "";
    try {
      const blob = await apiBlob("/api/voice/speak", { method: "POST", json: { text } });
      url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      audio.onended = () => { setPlaying(false); URL.revokeObjectURL(url); };
      audio.onerror = () => { setPlaying(false); URL.revokeObjectURL(url); onError("The spoken reply could not be played."); };
      await audio.play();
    } catch (caught) {
      if (url) URL.revokeObjectURL(url);
      setPlaying(false);
      onError(caught instanceof Error ? caught.message : "The spoken reply could not be created.");
    }
  }

  if (!enabled) return null;
  return <button className="quiet-button mt-3 text-xs" type="button" onClick={() => void speak()} disabled={playing}>{playing ? "Speaking…" : "Speak reply"}</button>;
}
