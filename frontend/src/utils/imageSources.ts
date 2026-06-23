const WEBP_CANDIDATE_PATTERN = /\.(png|jpe?g)(?=($|[?#]))/i;

export function toWebpSource(src: string | null | undefined) {
  if (!src || !WEBP_CANDIDATE_PATTERN.test(src)) return null;
  return src.replace(WEBP_CANDIDATE_PATTERN, '.webp');
}
