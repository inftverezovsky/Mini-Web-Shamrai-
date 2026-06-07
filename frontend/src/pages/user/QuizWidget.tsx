import React, { useEffect, useMemo, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { BrainCircuit, CheckCircle2, Loader2, X } from 'lucide-react';
import { apiFetch } from '../../utils/api';
import { QuizActiveResponse, QuizSubmitResponse } from '../../schemas/schemas';

export default function QuizWidget() {
  const [quiz, setQuiz] = useState<QuizActiveResponse | null>(null);
  const [open, setOpen] = useState(false);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [checking, setChecking] = useState(false);
  const [result, setResult] = useState<QuizSubmitResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function loadQuiz() {
      try {
        const data = await apiFetch('/marketing/quiz-active');
        setQuiz(data);
      } catch (err: any) {
        setError(err.message || 'Тест недоступен');
      }
    }

    loadQuiz();
  }, []);

  const completed = useMemo(() => {
    if (!quiz) return false;
    return quiz.questions.every((question) => answers[question.id]);
  }, [answers, quiz]);

  const submitQuiz = async () => {
    if (!quiz || !completed) return;

    try {
      setChecking(true);
      setResult(null);
      await new Promise((resolve) => setTimeout(resolve, 850));
      const data = await apiFetch('/marketing/quiz-submit', {
        method: 'POST',
        body: JSON.stringify({ quiz_id: quiz.id, answers }),
      });
      setResult(data);
    } catch (err: any) {
      setError(err.message || 'Не удалось проверить тест');
    } finally {
      setChecking(false);
    }
  };

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        disabled={!quiz}
        className="shimmer-border flex w-full items-center justify-between rounded-3xl border border-white/10 bg-white/[0.045] p-4 text-left shadow-glass backdrop-blur-xl active:scale-[0.99]"
      >
        <span>
          <span className="block text-[9px] font-black uppercase tracking-[0.22em] text-[#ff8fc7]">
            Аналитический тест
          </span>
          <span className="mt-1 block text-sm font-black text-white">
            Докажи логику и забери скидку {quiz?.discount_reward || 30}%
          </span>
          {error && <span className="mt-1 block text-[10px] font-bold text-rose-300">{error}</span>}
        </span>
        <BrainCircuit className="h-8 w-8 text-[#ff007f] drop-shadow-[0_0_14px_rgba(255,0,127,0.72)]" />
      </button>

      <AnimatePresence>
        {open && quiz && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-[80] flex items-center justify-center bg-slate-950/78 px-4 backdrop-blur-md"
          >
            <motion.div
              initial={{ opacity: 0, y: 24, scale: 0.96 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: 18, scale: 0.97 }}
              className="relative max-h-[88vh] w-full max-w-md overflow-y-auto rounded-3xl border border-white/15 bg-slate-950/88 p-5 shadow-glass"
            >
              <button
                onClick={() => setOpen(false)}
                className="absolute right-4 top-4 rounded-full border border-white/10 bg-white/10 p-1.5 text-slate-300"
              >
                <X className="h-4 w-4" />
              </button>

              <div className="mb-4 pr-9">
                <p className="text-[9px] font-black uppercase tracking-[0.22em] text-[#00d2ff]">
                  Shamrai Brain Check
                </p>
                <h3 className="mt-1 text-lg font-black text-white">Аналитический Тест Shamrai</h3>
              </div>

              <div className="space-y-3">
                {quiz.questions.map((question, index) => (
                  <div key={question.id} className="rounded-2xl border border-white/10 bg-white/[0.045] p-3">
                    <p className="text-xs font-black text-white">
                      {index + 1}. {question.question}
                    </p>
                    <div className="mt-3 space-y-2">
                      {question.options.map((option) => {
                        const selected = answers[question.id] === option;
                        return (
                          <button
                            key={option}
                            onClick={() => setAnswers((prev) => ({ ...prev, [question.id]: option }))}
                            className={`w-full rounded-xl border px-3 py-2 text-left text-[11px] font-bold transition ${
                              selected
                                ? 'border-[#00d2ff]/50 bg-[#00d2ff]/12 text-[#8eeaff] shadow-[0_0_14px_rgba(0,210,255,0.18)]'
                                : 'border-white/10 bg-white/[0.035] text-slate-300'
                            }`}
                          >
                            {option}
                          </button>
                        );
                      })}
                    </div>
                  </div>
                ))}
              </div>

              <button
                onClick={submitQuiz}
                disabled={!completed || checking}
                className="mt-4 flex w-full items-center justify-center gap-2 rounded-2xl bg-gradient-to-r from-[#00d2ff] to-[#ff007f] px-4 py-3 text-xs font-black text-white shadow-[0_0_22px_rgba(0,210,255,0.24)] disabled:opacity-45"
              >
                {checking ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin" />
                    Загрузка мозга
                  </>
                ) : (
                  'Проверить мышление'
                )}
              </button>

              <AnimatePresence>
                {result && (
                  <motion.div
                    initial={{ opacity: 0, y: 12 }}
                    animate={{ opacity: 1, y: 0 }}
                    className="mt-4 rounded-2xl border border-emerald-400/25 bg-emerald-400/10 p-4 text-center"
                  >
                    <CheckCircle2 className="mx-auto mb-2 h-8 w-8 text-emerald-300" />
                    <p className="text-sm font-black text-white">
                      {result.passed
                        ? `Тест пройден! Ваша логика безупречна. Держите скидку ${result.discount}%`
                        : `Результат ${result.score}/${result.total}. Еще один заход?`}
                    </p>
                    {result.promo_code && (
                      <p className="mt-2 rounded-xl border border-emerald-300/25 bg-black/20 px-3 py-2 font-mono text-xs font-black text-emerald-200">
                        {result.promo_code}
                      </p>
                    )}
                  </motion.div>
                )}
              </AnimatePresence>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  );
}
