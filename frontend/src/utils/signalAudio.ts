let sharedSignalAudioContext: AudioContext | null = null;
let signalAudioUnlocked = false;

export type IncomingSignalSoundState = 'locked' | 'unlocked' | 'unsupported';

function getSignalAudioContext() {
  if (typeof window === 'undefined') return null;
  if (sharedSignalAudioContext?.state === 'closed') {
    sharedSignalAudioContext = null;
    signalAudioUnlocked = false;
  }
  if (sharedSignalAudioContext) return sharedSignalAudioContext;

  const AudioContextClass = window.AudioContext
    || (window as typeof window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!AudioContextClass) return null;

  sharedSignalAudioContext = new AudioContextClass();
  return sharedSignalAudioContext;
}

async function getRunningSignalAudioContext() {
  const audioContext = getSignalAudioContext();
  if (!audioContext) return null;

  try {
    if (audioContext.state === 'suspended') {
      await audioContext.resume();
    }
  } catch {
    return null;
  }

  return audioContext.state === 'running' ? audioContext : null;
}

function createNotificationMaster(audioContext: AudioContext, startTime: number, volume: number) {
  const master = audioContext.createGain();
  const filter = audioContext.createBiquadFilter();
  const compressor = audioContext.createDynamicsCompressor();

  master.gain.setValueAtTime(0.0001, startTime);
  master.gain.exponentialRampToValueAtTime(volume, startTime + 0.025);
  master.gain.setTargetAtTime(0.0001, startTime + 0.42, 0.12);

  filter.type = 'lowpass';
  filter.frequency.setValueAtTime(3200, startTime);
  filter.Q.setValueAtTime(0.55, startTime);

  compressor.threshold.setValueAtTime(-24, startTime);
  compressor.knee.setValueAtTime(20, startTime);
  compressor.ratio.setValueAtTime(6, startTime);
  compressor.attack.setValueAtTime(0.004, startTime);
  compressor.release.setValueAtTime(0.22, startTime);

  master.connect(filter);
  filter.connect(compressor);
  compressor.connect(audioContext.destination);

  window.setTimeout(() => {
    master.disconnect();
    filter.disconnect();
    compressor.disconnect();
  }, 1000);

  return master;
}

function scheduleTone(
  audioContext: AudioContext,
  destination: AudioNode,
  startTime: number,
  offset: number,
  duration: number,
  fromHz: number,
  toHz: number,
  volume: number,
  type: OscillatorType = 'sine',
) {
  const oscillator = audioContext.createOscillator();
  const gain = audioContext.createGain();
  const toneStart = startTime + offset;
  const toneEnd = toneStart + duration;

  oscillator.type = type;
  oscillator.frequency.setValueAtTime(fromHz, toneStart);
  oscillator.frequency.exponentialRampToValueAtTime(toHz, toneEnd);

  gain.gain.setValueAtTime(0.0001, toneStart);
  gain.gain.exponentialRampToValueAtTime(volume, toneStart + 0.02);
  gain.gain.setTargetAtTime(0.0001, toneEnd - 0.075, 0.045);

  oscillator.connect(gain);
  gain.connect(destination);
  oscillator.start(toneStart);
  oscillator.stop(toneEnd + 0.04);
  oscillator.onended = () => {
    oscillator.disconnect();
    gain.disconnect();
  };
}

function scheduleWarmBell(audioContext: AudioContext, volume: number, delay = 0) {
  const startTime = audioContext.currentTime + 0.018 + delay;
  const master = createNotificationMaster(audioContext, startTime, volume);

  scheduleTone(audioContext, master, startTime, 0, 0.24, 392, 523.25, 0.22, 'triangle');
  scheduleTone(audioContext, master, startTime, 0.065, 0.28, 587.33, 698.46, 0.12, 'sine');
  scheduleTone(audioContext, master, startTime, 0, 0.38, 196, 164.81, 0.055, 'triangle');
}

async function playWarmNotificationSound({
  volume,
  repeatDelay,
}: {
  volume: number;
  repeatDelay?: number;
}) {
  const audioContext = await getRunningSignalAudioContext();
  if (!audioContext) return false;

  try {
    signalAudioUnlocked = true;
    scheduleWarmBell(audioContext, volume);
    if (repeatDelay) {
      scheduleWarmBell(audioContext, volume * 0.82, repeatDelay);
    }
    return true;
  } catch {
    return false;
  }
}

export async function unlockIncomingSignalSound() {
  const audioContext = await getRunningSignalAudioContext();
  if (!audioContext) return false;

  try {
    const oscillator = audioContext.createOscillator();
    const gain = audioContext.createGain();
    gain.gain.setValueAtTime(0.0001, audioContext.currentTime);
    oscillator.frequency.setValueAtTime(440, audioContext.currentTime);
    oscillator.connect(gain);
    gain.connect(audioContext.destination);
    oscillator.start();
    oscillator.stop(audioContext.currentTime + 0.03);
    oscillator.onended = () => {
      oscillator.disconnect();
      gain.disconnect();
    };
    signalAudioUnlocked = true;
    return true;
  } catch {
    return false;
  }
}

export function getIncomingSignalSoundState(): IncomingSignalSoundState {
  if (typeof window === 'undefined') return 'unsupported';
  const windowWithAudio = window as typeof window & { webkitAudioContext?: typeof AudioContext };
  const AudioContextClass = window.AudioContext || windowWithAudio.webkitAudioContext;
  if (!AudioContextClass) return 'unsupported';
  return signalAudioUnlocked ? 'unlocked' : 'locked';
}

export async function playIncomingSignalSound() {
  await playWarmNotificationSound({ volume: 0.46 });
}

export async function playIncomingSupportSound() {
  if (signalAudioUnlocked && typeof navigator !== 'undefined') {
    navigator.vibrate?.([45]);
  }
  await playWarmNotificationSound({ volume: 0.54 });
}
