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

    const oscillator = audioContext.createOscillator();
    const gain = audioContext.createGain();
    oscillator.type = 'sine';
    oscillator.frequency.setValueAtTime(880, audioContext.currentTime);
    oscillator.frequency.exponentialRampToValueAtTime(1320, audioContext.currentTime + 0.08);
    gain.gain.setValueAtTime(0.0001, audioContext.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.12, audioContext.currentTime + 0.015);
    gain.gain.exponentialRampToValueAtTime(0.0001, audioContext.currentTime + 0.22);
    oscillator.connect(gain);
    gain.connect(audioContext.destination);
    oscillator.start();
    oscillator.stop(audioContext.currentTime + 0.24);
  } catch {
    // Browsers may block audio until the first user gesture.
  }
}
