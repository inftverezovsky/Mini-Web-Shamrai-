import React from 'react';
import { AlertTriangle, RotateCcw } from 'lucide-react';

type AppErrorBoundaryProps = {
  children: React.ReactNode;
  resetKey?: string | number;
  title?: string;
  description?: string;
};

type AppErrorBoundaryState = {
  error: Error | null;
};

const CHUNK_LOAD_ERROR_PATTERNS = [
  /Failed to fetch dynamically imported module/i,
  /Importing a module script failed/i,
  /Loading chunk \d+ failed/i,
  /ChunkLoadError/i,
];

function isChunkLoadError(error: Error | null) {
  if (!error) return false;
  const text = `${error.name || ''} ${error.message || ''}`;
  return CHUNK_LOAD_ERROR_PATTERNS.some((pattern) => pattern.test(text));
}

function currentChunkReloadKey() {
  if (typeof document === 'undefined') return 'shamrai:chunk-reload:unknown';
  const entryScript = document.querySelector<HTMLScriptElement>('script[type="module"][src*="/assets/"]')?.src || 'dev';
  return `shamrai:chunk-reload:${entryScript}`;
}

function canReloadChunkOnce() {
  if (typeof window === 'undefined') return false;
  try {
    const key = currentChunkReloadKey();
    if (window.sessionStorage.getItem(key) === '1') return false;
    window.sessionStorage.setItem(key, '1');
    return true;
  } catch {
    return false;
  }
}

export default class AppErrorBoundary extends React.Component<AppErrorBoundaryProps, AppErrorBoundaryState> {
  state: AppErrorBoundaryState = {
    error: null,
  };

  static getDerivedStateFromError(error: Error): AppErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error('App section crashed', error, info);
  }

  componentDidUpdate(prevProps: AppErrorBoundaryProps) {
    if (this.state.error && prevProps.resetKey !== this.props.resetKey) {
      this.setState({ error: null });
    }
  }

  private handleRetry = () => {
    if (isChunkLoadError(this.state.error) && canReloadChunkOnce()) {
      window.location.reload();
      return;
    }
    this.setState({ error: null });
  };

  render() {
    if (!this.state.error) {
      return this.props.children;
    }

    return (
      <div className="flex min-h-[42vh] flex-col items-center justify-center gap-4 rounded-3xl border border-slate-400/20 bg-slate-950/20 px-5 py-10 text-center text-slate-50">
        <div className="flex h-14 w-14 items-center justify-center rounded-2xl border border-slate-300/20 bg-slate-500/10">
          <AlertTriangle className="h-7 w-7 text-slate-300" />
        </div>
        <div className="space-y-2">
          <h2 className="text-base font-black text-white">
            {this.props.title ?? 'Раздел временно недоступен'}
          </h2>
          <p className="mx-auto max-w-sm text-xs font-semibold leading-relaxed text-slate-300">
            {this.props.description ?? 'Мы уже изолировали сбой, остальные разделы можно открыть через навигацию.'}
          </p>
        </div>
        <button
          type="button"
          onClick={this.handleRetry}
          className="inline-flex items-center gap-2 rounded-2xl border border-white/10 bg-white/10 px-4 py-2 text-xs font-black text-white transition-all hover:bg-white/15 active:scale-95"
        >
          <RotateCcw className="h-4 w-4 text-emerald-300" />
          Повторить
        </button>
      </div>
    );
  }
}
