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
    this.setState({ error: null });
  };

  render() {
    if (!this.state.error) {
      return this.props.children;
    }

    return (
      <div className="flex min-h-[42vh] flex-col items-center justify-center gap-4 rounded-3xl border border-rose-400/20 bg-rose-950/20 px-5 py-10 text-center text-slate-50">
        <div className="flex h-14 w-14 items-center justify-center rounded-2xl border border-rose-300/20 bg-rose-500/10">
          <AlertTriangle className="h-7 w-7 text-rose-300" />
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
