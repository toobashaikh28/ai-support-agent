const token = localStorage.getItem("user_token");
const userJson = localStorage.getItem("user_info");

if (!token) {
  window.location.href = "login.html";
}

const user = userJson ? JSON.parse(userJson) : null;
if (user) {
  document.getElementById("user-info").textContent = `${user.name}`;
}

document.getElementById("logout-link").addEventListener("click", () => {
  localStorage.removeItem("user_token");
  localStorage.removeItem("user_info");
  window.location.href = "login.html";
});

function authHeaders(extra = {}) {
  return { Authorization: `Bearer ${token}`, ...extra };
}

async function apiFetch(url, options = {}) {
  const res = await fetch(url, {
    ...options,
    headers: { ...(options.headers || {}), ...authHeaders() },
  });
  if (res.status === 401) {
    localStorage.removeItem("user_token");
    localStorage.removeItem("user_info");
    window.location.href = "login.html";
    throw new Error("Session expired");
  }
  return res;
}

const messagesArea = document.getElementById("messages-area");
const composerForm = document.getElementById("composer-form");
const messageInput = document.getElementById("message-input");
const sendBtn = document.getElementById("send-btn");

function formatTime(iso) {
  const d = new Date(iso);
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function renderMessages(messages) {
  messagesArea.innerHTML = "";

  if (!messages.length) {
    const empty = document.createElement("div");
    empty.className = "empty-chat";
    empty.id = "empty-chat";
    empty.textContent = "Ask a question to get started.";
    messagesArea.appendChild(empty);
    return;
  }

  messages.forEach((m) => messagesArea.appendChild(renderMessageRow(m)));
  scrollToBottom();
}

function renderMessageRow(m) {
  const row = document.createElement("div");
  row.className = `message-row ${m.sender === "customer" ? "from-user" : "from-agent"}`;
  row.dataset.messageId = m.id;

  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = m.body;
  row.appendChild(bubble);

  if (m.citations && m.citations.length) {
    const citeWrap = document.createElement("div");
    m.citations.forEach((c) => {
      const tag = document.createElement("span");
      tag.className = "citation-tag";
      tag.textContent = c.source;
      citeWrap.appendChild(tag);
    });
    row.appendChild(citeWrap);
  }

  const meta = document.createElement("div");
  meta.className = "msg-meta";

  const time = document.createElement("span");
  time.textContent = formatTime(m.timestamp);
  meta.appendChild(time);

  if (m.sender === "agent") {
    const fbWrap = document.createElement("span");
    fbWrap.className = "feedback-buttons";

    const upBtn = document.createElement("button");
    upBtn.className = `feedback-btn ${m.feedback === "good" ? "active-good" : ""}`;
    upBtn.textContent = "👍";
    upBtn.title = "Good response";
    upBtn.addEventListener("click", () => sendFeedback(m.id, "good", upBtn, downBtn));

    const downBtn = document.createElement("button");
    downBtn.className = `feedback-btn ${m.feedback === "bad" ? "active-bad" : ""}`;
    downBtn.textContent = "👎";
    downBtn.title = "Bad response";
    downBtn.addEventListener("click", () => sendFeedback(m.id, "bad", upBtn, downBtn));

    const speakBtn = document.createElement("button");
    speakBtn.className = "speaker-btn";
    speakBtn.textContent = "🔊";
    speakBtn.title = "Read this reply aloud";
    speakBtn.addEventListener("click", () => speakText(m.body, speakBtn));

    fbWrap.appendChild(upBtn);
    fbWrap.appendChild(downBtn);
    fbWrap.appendChild(speakBtn);
    meta.appendChild(fbWrap);
  }

  row.appendChild(meta);
  return row;
}

async function sendFeedback(messageId, value, upBtn, downBtn) {
  upBtn.classList.remove("active-good");
  downBtn.classList.remove("active-bad");
  if (value === "good") upBtn.classList.add("active-good");
  else downBtn.classList.add("active-bad");

  try {
    await apiFetch(`/chat/messages/${messageId}/feedback`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ feedback: value }),
    });
  } catch (err) {
    // silently ignore - the UI already reflects the intended state
  }
}

function scrollToBottom() {
  messagesArea.scrollTop = messagesArea.scrollHeight;
}

function showEscalationBanner(escalation) {
  const existing = document.getElementById("escalation-banner");
  if (existing) existing.remove();
  if (!escalation) return;

  const banner = document.createElement("div");
  banner.className = "escalation-banner";
  banner.id = "escalation-banner";
  banner.innerHTML = escalation.already_escalated
    ? `This conversation is already with a human agent — reference <b>${escalation.ticket_reference}</b>.`
    : `This conversation has been passed to a human agent — reference <b>${escalation.ticket_reference}</b>. Someone will follow up shortly.`;
  messagesArea.parentElement.insertBefore(banner, messagesArea.nextSibling);
}

function showTypingIndicator() {
  const el = document.createElement("div");
  el.className = "typing-indicator";
  el.id = "typing-indicator";
  el.textContent = "Support is typing...";
  messagesArea.appendChild(el);
  scrollToBottom();
}

function hideTypingIndicator() {
  const el = document.getElementById("typing-indicator");
  if (el) el.remove();
}

async function loadHistory() {
  const res = await apiFetch("/chat/me");
  const data = await res.json();
  renderMessages(data.messages || []);
  if (data.open_ticket_reference) {
    showEscalationBanner({ ticket_reference: data.open_ticket_reference, already_escalated: true });
  }
}

composerForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = messageInput.value.trim();
  if (!text) return;

  messageInput.value = "";
  sendBtn.disabled = true;

  // optimistic render of the user's own message
  const emptyEl = document.getElementById("empty-chat");
  if (emptyEl) emptyEl.remove();
  const tempRow = renderMessageRow({
    id: `temp-${Date.now()}`,
    sender: "customer",
    body: text,
    timestamp: new Date().toISOString(),
  });
  messagesArea.appendChild(tempRow);
  showTypingIndicator();
  scrollToBottom();

  try {
    const res = await apiFetch("/chat/me", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text }),
    });
    const data = await res.json();
    hideTypingIndicator();

    if (!res.ok) {
      alert(data.detail || "Something went wrong sending that message.");
      return;
    }

    // re-fetch full history so IDs/timestamps/feedback state are all correct
    await loadHistory();
    showEscalationBanner(data.escalation);
  } catch (err) {
    hideTypingIndicator();
    if (err.message !== "Session expired") {
      alert("Could not reach the server.");
    }
  } finally {
    sendBtn.disabled = false;
    messageInput.focus();
  }
});

loadHistory();

// --------------------------------------------------------------------------
// Voice input: record -> upload to /chat/me/voice -> transcript is run
// through the exact same grounded-reply pipeline as typed messages.
// Voice output: read the reply aloud client-side (Web Speech API) - no
// audio file ever comes back from the server, so nothing new to host or
// clean up on disk.
// --------------------------------------------------------------------------

const micBtn = document.getElementById("mic-btn");
const voiceStatus = document.getElementById("voice-status");

let mediaRecorder = null;
let audioChunks = [];
let isRecording = false;

function pickMimeType() {
  const candidates = ["audio/webm", "audio/ogg", "audio/mp4"];
  for (const type of candidates) {
    if (window.MediaRecorder && MediaRecorder.isTypeSupported(type)) return type;
  }
  return ""; // let the browser choose its default
}

function setVoiceStatus(text) {
  voiceStatus.textContent = text;
}

async function startRecording() {
  if (!navigator.mediaDevices || !window.MediaRecorder) {
    alert("Voice recording isn't supported in this browser. Try Chrome or Edge.");
    return;
  }

  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (err) {
    alert("Microphone permission was denied — voice input needs mic access to work.");
    return;
  }

  const mimeType = pickMimeType();
  mediaRecorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
  audioChunks = [];

  mediaRecorder.addEventListener("dataavailable", (e) => {
    if (e.data.size > 0) audioChunks.push(e.data);
  });

  mediaRecorder.addEventListener("stop", () => {
    stream.getTracks().forEach((track) => track.stop()); // release the mic
    const blob = new Blob(audioChunks, { type: mediaRecorder.mimeType || "audio/webm" });
    sendVoiceMessage(blob, mediaRecorder.mimeType || "audio/webm");
  });

  mediaRecorder.start();
  isRecording = true;
  micBtn.classList.add("recording");
  micBtn.textContent = "⏹";
  setVoiceStatus("Recording... click again to stop.");
}

function stopRecording() {
  if (mediaRecorder && isRecording) {
    mediaRecorder.stop();
  }
  isRecording = false;
  micBtn.classList.remove("recording");
  micBtn.textContent = "🎤";
}

micBtn.addEventListener("click", () => {
  if (isRecording) {
    stopRecording();
  } else {
    startRecording();
  }
});

function extensionForMimeType(mimeType) {
  if (mimeType.includes("webm")) return "webm";
  if (mimeType.includes("ogg")) return "ogg";
  if (mimeType.includes("mp4")) return "m4a";
  if (mimeType.includes("wav")) return "wav";
  return "webm";
}

async function sendVoiceMessage(blob, mimeType) {
  setVoiceStatus("Transcribing your message...");
  micBtn.disabled = true;
  sendBtn.disabled = true;

  const emptyEl = document.getElementById("empty-chat");
  if (emptyEl) emptyEl.remove();
  showTypingIndicator();

  const formData = new FormData();
  formData.append("audio", blob, `voice.${extensionForMimeType(mimeType)}`);

  try {
    const res = await apiFetch("/chat/me/voice", {
      method: "POST",
      body: formData, // no Content-Type header set on purpose - the browser
                       // adds the correct multipart boundary itself
    });
    const data = await res.json();
    hideTypingIndicator();

    if (!res.ok) {
      setVoiceStatus("");
      alert(data.detail || "Couldn't process that voice message.");
      return;
    }

    setVoiceStatus(`Heard: "${data.transcript}"`);
    await loadHistory();
    showEscalationBanner(data.escalation);
    speakText(data.reply); // voice out, automatically, for the voice-in turn
  } catch (err) {
    hideTypingIndicator();
    setVoiceStatus("");
    if (err.message !== "Session expired") {
      alert("Could not reach the server.");
    }
  } finally {
    micBtn.disabled = false;
    sendBtn.disabled = false;
  }
}

// Tracks the currently playing natural-voice clip so a new speakText()
// call can stop it first, the same role speechSynthesis.cancel() used to
// play for the old browser-voice version.
let currentSpeechAudio = null;

function speakWithBrowserFallback(text, triggerBtn) {
  // Used only if the natural-voice API call fails (network issue, quota,
  // etc.) - keeps voice output working end-to-end rather than going
  // silent, just in the old robotic voice for that one reply.
  if (!window.speechSynthesis) return;
  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.rate = 1.0;
  if (triggerBtn) {
    triggerBtn.classList.add("speaking");
    utterance.addEventListener("end", () => triggerBtn.classList.remove("speaking"));
    utterance.addEventListener("error", () => triggerBtn.classList.remove("speaking"));
  }
  window.speechSynthesis.speak(utterance);
}

async function speakText(text, triggerBtn = null) {
  if (currentSpeechAudio) {
    currentSpeechAudio.pause();
    currentSpeechAudio = null;
  }
  window.speechSynthesis?.cancel(); // in case a fallback clip is mid-playback

  if (triggerBtn) triggerBtn.classList.add("speaking");

  try {
    const res = await apiFetch("/chat/me/speak", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    if (!res.ok) throw new Error("TTS request failed");

    const blob = await res.blob();
    const audio = new Audio(URL.createObjectURL(blob));
    currentSpeechAudio = audio;

    audio.addEventListener("ended", () => {
      if (triggerBtn) triggerBtn.classList.remove("speaking");
      currentSpeechAudio = null;
    });
    audio.addEventListener("error", () => {
      if (triggerBtn) triggerBtn.classList.remove("speaking");
      currentSpeechAudio = null;
    });

    await audio.play();
  } catch (err) {
    console.warn("Natural voice unavailable, falling back to browser voice:", err);
    speakWithBrowserFallback(text, triggerBtn);
  }
}

// --------------------------------------------------------------------------
// Live call: a "Call" button that opens a full-screen overlay with a
// continuous listen -> think -> speak -> listen loop, so the customer
// talks the way they would on an actual phone call instead of recording
// and sending one clip at a time (that's the mic button on the composer).
//
// This uses the browser's built-in SpeechRecognition for STT - it's
// streaming and near-instant, which matters for something that's supposed
// to feel live. Recognition is paused while the AI is thinking/speaking so
// it doesn't pick up the AI's own voice as new input, then resumes
// automatically. Every turn goes through the exact same /chat/me endpoint
// as typed messages, so grounding, tone-switching, and escalation all work
// identically on a call.
// --------------------------------------------------------------------------

const callBtn = document.getElementById("call-btn");
const callOverlay = document.getElementById("call-overlay");
const callClose = document.getElementById("call-close");
const callEndBtn = document.getElementById("call-end-btn");
const callOrb = document.getElementById("call-orb");
const callOrbIcon = document.getElementById("call-orb-icon");
const callStateEl = document.getElementById("call-state");
const callHint = document.getElementById("call-hint");
const callTranscript = document.getElementById("call-transcript");

const SpeechRecognitionAPI = window.SpeechRecognition || window.webkitSpeechRecognition;

let recognition = null;
let callActive = false;
let stoppedForProcessing = false; // true when *we* stopped recognition to handle a turn,
                                   // vs. the browser auto-stopping after silence

function setCallState(state) {
  callOrb.classList.remove("listening", "thinking", "speaking");
  if (state === "listening") {
    callOrb.classList.add("listening");
    callOrbIcon.textContent = "🎤";
    callStateEl.textContent = "Listening...";
    callHint.textContent = "Speak naturally — the agent will respond out loud.";
  } else if (state === "thinking") {
    callOrb.classList.add("thinking");
    callOrbIcon.textContent = "💭";
    callStateEl.textContent = "Thinking...";
    callHint.textContent = "";
  } else if (state === "speaking") {
    callOrb.classList.add("speaking");
    callOrbIcon.textContent = "🔊";
    callStateEl.textContent = "Speaking...";
    callHint.textContent = "";
  } else {
    callOrbIcon.textContent = "📞";
    callStateEl.textContent = "Connecting...";
    callHint.textContent = "";
  }
}

function addCallLine(speaker, text, interim = false) {
  const existingInterim = callTranscript.querySelector(".call-line.interim");
  if (existingInterim) existingInterim.remove();

  const line = document.createElement("div");
  line.className = "call-line" + (interim ? " interim" : "");
  line.innerHTML = `<b>${speaker}:</b> ${text}`;
  callTranscript.appendChild(line);
  callTranscript.scrollTop = callTranscript.scrollHeight;
}

function initRecognition() {
  recognition = new SpeechRecognitionAPI();
  recognition.continuous = true;
  recognition.interimResults = true;
  recognition.lang = "en-US";

  recognition.addEventListener("result", (event) => {
    let interimText = "";
    for (let i = event.resultIndex; i < event.results.length; i++) {
      const result = event.results[i];
      if (result.isFinal) {
        const finalText = result[0].transcript.trim();
        if (finalText) {
          addCallLine("You", finalText);
          stoppedForProcessing = true;
          recognition.stop();
          handleCallTurn(finalText);
        }
        return;
      }
      interimText += result[0].transcript;
    }
    if (interimText.trim()) {
      addCallLine("You", interimText, true);
    }
  });

  recognition.addEventListener("end", () => {
    // The browser stops recognition on its own after a stretch of silence.
    // If the call is still active and we didn't stop it ourselves to
    // process a turn, just restart listening - that's what makes it feel
    // continuous rather than push-to-talk.
    if (callActive && !stoppedForProcessing) {
      try {
        recognition.start();
      } catch (err) {
        // already running - ignore
      }
    }
    stoppedForProcessing = false;
  });

  recognition.addEventListener("error", (event) => {
    if (event.error === "no-speech" || event.error === "aborted") {
      return; // expected during normal pauses, onend will restart it
    }
    if (event.error === "not-allowed" || event.error === "audio-capture") {
      addCallLine("System", "Microphone access is blocked - please allow mic access and try again.");
      endCall();
    }
  });
}

async function handleCallTurn(userText) {
  setCallState("thinking");

  try {
    const res = await apiFetch("/chat/me", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: userText }),
    });
    const data = await res.json();

    if (!res.ok) {
      addCallLine("System", data.detail || "Something went wrong.");
      if (callActive) resumeListening();
      return;
    }

    addCallLine("Agent", data.reply);
    if (data.escalation) showEscalationBanner(data.escalation);

    setCallState("speaking");
    speakOnCall(data.reply);
  } catch (err) {
    addCallLine("System", "Could not reach the server.");
    if (callActive) resumeListening();
  }
}

async function speakOnCall(text) {
  if (currentSpeechAudio) {
    currentSpeechAudio.pause();
    currentSpeechAudio = null;
  }
  window.speechSynthesis?.cancel();

  try {
    const res = await apiFetch("/chat/me/speak", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    if (!res.ok) throw new Error("TTS request failed");

    const blob = await res.blob();
    const audio = new Audio(URL.createObjectURL(blob));
    currentSpeechAudio = audio;

    audio.addEventListener("ended", () => {
      currentSpeechAudio = null;
      if (callActive) resumeListening();
    });
    audio.addEventListener("error", () => {
      currentSpeechAudio = null;
      if (callActive) resumeListening();
    });

    await audio.play();
  } catch (err) {
    console.warn("Natural voice unavailable on call, falling back to browser voice:", err);
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.rate = 1.0;
    utterance.addEventListener("end", () => { if (callActive) resumeListening(); });
    utterance.addEventListener("error", () => { if (callActive) resumeListening(); });
    window.speechSynthesis.speak(utterance);
  }
}

function resumeListening() {
  setCallState("listening");
  try {
    recognition.start();
  } catch (err) {
    // already running - ignore
  }
}

function startCall() {
  if (!SpeechRecognitionAPI) {
    alert("Live calling needs Chrome or Edge — this browser doesn't support real-time speech recognition.");
    return;
  }
  if (!window.speechSynthesis) {
    alert("Voice output isn't supported in this browser. Try Chrome or Edge.");
    return;
  }

  callTranscript.innerHTML = "";
  callOverlay.classList.add("active");
  setCallState(null);
  callActive = true;

  initRecognition();

  navigator.mediaDevices
    .getUserMedia({ audio: true })
    .then(() => {
      resumeListening();
      addCallLine("System", "Call connected.");
    })
    .catch(() => {
      addCallLine("System", "Microphone permission was denied.");
      endCall();
    });
}

function endCall() {
  callActive = false;
  window.speechSynthesis.cancel();
  if (currentSpeechAudio) {
    currentSpeechAudio.pause();
    currentSpeechAudio = null;
  }
  if (recognition) {
    stoppedForProcessing = true; // prevent the onend auto-restart
    recognition.stop();
    recognition = null;
  }
  callOverlay.classList.remove("active");
  loadHistory(); // pull the call's messages into the regular chat view
}

callBtn.addEventListener("click", startCall);
callClose.addEventListener("click", endCall);
callEndBtn.addEventListener("click", endCall);
