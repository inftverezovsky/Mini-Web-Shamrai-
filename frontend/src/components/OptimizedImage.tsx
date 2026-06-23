import type { ImgHTMLAttributes } from 'react';

interface OptimizedImageProps extends ImgHTMLAttributes<HTMLImageElement> {
  webpSrc?: string | null;
}

export default function OptimizedImage({
  webpSrc,
  src,
  loading = 'lazy',
  decoding = 'async',
  ...imageProps
}: OptimizedImageProps) {
  return (
    <picture>
      {webpSrc && <source srcSet={webpSrc} type="image/webp" />}
      <img
        {...imageProps}
        src={src}
        loading={loading}
        decoding={decoding}
      />
    </picture>
  );
}
