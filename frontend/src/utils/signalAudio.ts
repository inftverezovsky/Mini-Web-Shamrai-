let sharedSignalAudioContext: AudioContext | null = null;
let signalAudioUnlocked = false;

function getSignalAudioContext() {
  if (typeof window === 'undefined') return null;
  if (sharedSignalAudioContext) return sharedSignalAudioContext;

  const AudioContextClass = window.AudioContext
    || (window as typeof window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!AudioContextClass) return null;

  sharedSignalAudioContext = new AudioContextClass();
  return sharedSignalAudioContext;
}

export async function unlockIncomingSignalSound() {
  const audioContext = getSignalAudioContext();
  if (!audioContext) return false;

  try {
    if (audioContext.state === 'suspended') {
      await audioContext.resume();
    }

    const oscillator = audioContext.createOscillator();
    const gain = audioContext.createGain();
    gain.gain.setValueAtTime(0.0001, audioContext.currentTime);
    oscillator.frequency.setValueAtTime(440, audioContext.currentTime);
    oscillator.connect(gain);
    gain.connect(audioContext.destination);
    oscillator.start();
    oscillator.stop(audioContext.currentTime + 0.03);
    signalAudioUnlocked = true;
    return true;
  } catch {
    return false;
  }
}

export async function playIncomingSignalSound() {
  const audioContext = getSignalAudioContext();
  if (!audioContext) return;

  try {
    if (audioContext.state === 'suspended') {
      await audioContext.resume();
    }
    if (audioContext.state === 'suspended' && !signalAudioUnlocked) return;

    const startTime = audioContext.currentTime + 0.01;
    const master = audioContext.createGain();
    const compressor = audioContext.createDynamicsCompressor();

    master.gain.setValueAtTime(0.95, startTime);
    compressor.threshold.setValueAtTime(-18, startTime);
    compressor.knee.setValueAtTime(18, startTime);
    compressor.ratio.setValueAtTime(6, startTime);
    compressor.attack.setValueAtTime(0.004, startTime);
    compressor.release.setValueAtTime(0.18, startTime);
    master.connect(compressor);
    compressor.connect(audioContext.destination);

    const playTone = (
      offset: number,
      duration: number,
      fromHz: number,
      toHz: number,
      volume: number,
      type: OscillatorType = 'sine',
    ) => {
      const oscillator = audioContext.createOscillator();
      const gain = audioContext.createGain();
      const toneStart = startTime + offset;
      const toneEnd = toneStart + duration;

      oscillator.type = type;
      oscillator.frequency.setValueAtTime(fromHz, toneStart);
      oscillator.frequency.exponentialRampToValueAtTime(toHz, toneEnd);
      gain.gain.setValueAtTime(0.0001, toneStart);
      gain.gain.exponentialRampToValueAtTime(volume, toneStart + 0.018);
      gain.gain.exponentialRampToValueAtTime(0.0001, toneEnd);
      oscillator.connect(gain);
      gain.connect(master);
      oscillator.start(toneStart);
      oscillator.stop(toneEnd + 0.02);
    };

    const playClick = () => {
      const bufferSize = Math.floor(audioContext.sampleRate * 0.045);
      const buffer = audioContext.createBuffer(1, bufferSize, audioContext.sampleRate);
      const channelData = buffer.getChannelData(0);
      for (let index = 0; index < bufferSize; index += 1) {
        channelData[index] = (Math.random() * 2 - 1) * (1 - index / bufferSize);
      }

      const source = audioContext.createBufferSource();
      const filter = audioContext.createBiquadFilter();
      const gain = audioContext.createGain();
      filter.type = 'highpass';
      filter.frequency.setValueAtTime(2600, startTime);
      gain.gain.setValueAtTime(0.0001, startTime);
      gain.gain.exponentialRampToValueAtTime(0.055, startTime + 0.006);
      gain.gain.exponentialRampToValueAtTime(0.0001, startTime + 0.05);
      source.buffer = buffer;
      source.connect(filter);
      filter.connect(gain);
      gain.connect(master);
      source.start(startTime);
      source.stop(startTime + 0.06);
    };

    playClick();
    playTone(0, 0.11, 523.25, 659.25, 0.12, 'triangle');
    playTone(0.075, 0.12, 783.99, 987.77, 0.145, 'sine');
    playTone(0.165, 0.18, 1174.66, 1567.98, 0.17, 'sine');
    playTone(0, 0.22, 146.83, 98, 0.05, 'triangle');
  } catch {
    // Browsers may block audio until the first user gesture.
  }
}

export async function playIncomingSupportSound() {
  const audioContext = getSignalAudioContext();
  if (!audioContext) return;

  try {
    if (audioContext.state === 'suspended') {
      await audioContext.resume();
    }
    if (audioContext.state === 'suspended' && !signalAudioUnlocked) return;

    const startTime = audioContext.currentTime + 0.01;
    const master = audioContext.createGain();
    const compressor = audioContext.createDynamicsCompressor();

    master.gain.setValueAtTime(1.15, startTime);
    compressor.threshold.setValueAtTime(-20, startTime);
    compressor.knee.setValueAtTime(14, startTime);
    compressor.ratio.setValueAtTime(8, startTime);
    compressor.attack.setValueAtTime(0.003, startTime);
    compressor.release.setValueAtTime(0.24, startTime);
    master.connect(compressor);
    compressor.connect(audioContext.destination);

    const playTone = (
      offset: number,
      duration: number,
      fromHz: number,
      toHz: number,
      volume: number,
      type: OscillatorType = 'triangle',
    ) => {
      const oscillator = audioContext.createOscillator();
      const gain = audioContext.createGain();
      const toneStart = startTime + offset;
      const toneEnd = toneStart + duration;

      oscillator.type = type;
      oscillator.frequency.setValueAtTime(fromHz, toneStart);
      oscillator.frequency.exponentialRampToValueAtTime(toHz, toneEnd);
      gain.gain.setValueAtTime(0.0001, toneStart);
      gain.gain.exponentialRampToValueAtTime(volume, toneStart + 0.016);
      gain.gain.exponentialRampToValueAtTime(0.0001, toneEnd);
      oscillator.connect(gain);
      gain.connect(master);
      oscillator.start(toneStart);
      oscillator.stop(toneEnd + 0.025);
    };

    playTone(0, 0.16, 392, 523.25, 0.15);
    playTone(0.12, 0.18, 523.25, 783.99, 0.18, 'sine');
    playTone(0.29, 0.2, 659.25, 987.77, 0.2, 'sine');
    playTone(0.5, 0.22, 783.99, 1174.66, 0.18, 'sine');
    playTone(0.06, 0.65, 130.81, 98, 0.07, 'triangle');
  } catch {
    // Browsers may block audio until the first user gesture.
  }
}
