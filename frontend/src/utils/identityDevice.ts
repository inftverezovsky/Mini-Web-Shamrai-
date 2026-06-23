export const IDENTITY_DEVICE_HEADER = 'X-Shamrai-Device-Id';

const IDENTITY_DEVICE_STORAGE_KEY = 'shamrai_identity_device_id';

function fallbackUuid() {
  const randomPart = () => Math.floor((1 + Math.random()) * 0x10000).toString(16).slice(1);
  return `${randomPart()}${randomPart()}-${randomPart()}-${randomPart()}-${randomPart()}-${randomPart()}${randomPart()}${randomPart()}`;
}

export function getIdentityDeviceId() {
  const stored = localStorage.getItem(IDENTITY_DEVICE_STORAGE_KEY);
  if (stored) return stored;

  const nextId = typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : fallbackUuid();
  localStorage.setItem(IDENTITY_DEVICE_STORAGE_KEY, nextId);
  return nextId;
}

export function identityDeviceHeader(): Record<string, string> {
  return { [IDENTITY_DEVICE_HEADER]: getIdentityDeviceId() };
}
