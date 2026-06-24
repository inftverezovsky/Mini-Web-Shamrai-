type ClipboardFileItem = {
  kind?: string;
  type?: string;
  getAsFile?: () => File | null;
};

type ClipboardFileList = ArrayLike<File | null | undefined>;
type ClipboardItemList = ArrayLike<ClipboardFileItem | null | undefined>;

export type ClipboardDataLike = {
  items?: ClipboardItemList | null;
  files?: ClipboardFileList | null;
};

interface ClipboardImageOptions {
  now?: () => number;
}

function imageExtension(mimeType: string) {
  if (mimeType === 'image/jpeg') return 'jpg';
  if (mimeType === 'image/webp') return 'webp';
  if (mimeType === 'image/gif') return 'gif';
  return 'png';
}

function normalizeClipboardFile(file: File, options: ClipboardImageOptions) {
  if (!file.type.toLowerCase().startsWith('image/')) return null;
  if (file.name.trim()) return file;
  if (typeof File === 'undefined') return file;

  const timestamp = options.now?.() ?? Date.now();
  const extension = imageExtension(file.type.toLowerCase());
  return new File([file], `clipboard-${timestamp}.${extension}`, {
    type: file.type,
    lastModified: file.lastModified || timestamp,
  });
}

function imageFromItems(items: ClipboardItemList, options: ClipboardImageOptions) {
  for (let index = 0; index < items.length; index += 1) {
    const item = items[index];
    if (!item || item.kind !== 'file' || !item.type?.toLowerCase().startsWith('image/')) continue;
    const file = item.getAsFile?.();
    if (!file) continue;
    const normalizedFile = normalizeClipboardFile(file, options);
    if (normalizedFile) return normalizedFile;
  }
  return null;
}

function imageFromFiles(files: ClipboardFileList, options: ClipboardImageOptions) {
  for (let index = 0; index < files.length; index += 1) {
    const file = files[index];
    if (!file) continue;
    const normalizedFile = normalizeClipboardFile(file, options);
    if (normalizedFile) return normalizedFile;
  }
  return null;
}

export function getClipboardImageFile(
  clipboardData: ClipboardDataLike | null | undefined,
  options: ClipboardImageOptions = {},
) {
  if (!clipboardData) return null;
  if (clipboardData.items?.length) {
    const itemFile = imageFromItems(clipboardData.items, options);
    if (itemFile) return itemFile;
  }
  if (clipboardData.files?.length) {
    return imageFromFiles(clipboardData.files, options);
  }
  return null;
}
