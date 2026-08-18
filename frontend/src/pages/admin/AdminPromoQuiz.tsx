import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Plus, Trash2, Save, Loader2, PlayCircle } from 'lucide-react';
import { apiFetch } from '../../utils/api';
import { QuizResponse } from '../../schemas/schemas';

interface QuestionForm {
  question: string;
  options: string[];
  correct_answer_index: number;
}

export default function AdminPromoQuiz() {
  const queryClient = useQueryClient();
  const [isCreating, setIsCreating] = useState(false);
  const [discount, setDiscount] = useState(30);
  const [questions, setQuestions] = useState<QuestionForm[]>([
    { question: '', options: ['', ''], correct_answer_index: 0 },
  ]);

  const { data: quizzes, isLoading } = useQuery({
    queryKey: ['admin-quizzes'],
    queryFn: () => apiFetch<QuizResponse[]>('/admin/quizzes'),
  });

  const createMutation = useMutation({
    mutationFn: (data: { discount_reward: number; questions: QuestionForm[] }) =>
      apiFetch('/admin/quizzes', {
        method: 'POST',
        body: JSON.stringify(data),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin-quizzes'] });
      setIsCreating(false);
      setQuestions([{ question: '', options: ['', ''], correct_answer_index: 0 }]);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => apiFetch(`/admin/quizzes/${id}`, { method: 'DELETE' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['admin-quizzes'] }),
  });

  const addQuestion = () => {
    setQuestions([...questions, { question: '', options: ['', ''], correct_answer_index: 0 }]);
  };

  const removeQuestion = (index: number) => {
    setQuestions(questions.filter((_, i) => i !== index));
  };

  const addOption = (qIndex: number) => {
    const newQuestions = [...questions];
    newQuestions[qIndex].options.push('');
    setQuestions(newQuestions);
  };

  const removeOption = (qIndex: number, oIndex: number) => {
    const newQuestions = [...questions];
    newQuestions[qIndex].options = newQuestions[qIndex].options.filter((_, i) => i !== oIndex);
    if (newQuestions[qIndex].correct_answer_index >= newQuestions[qIndex].options.length) {
      newQuestions[qIndex].correct_answer_index = 0;
    }
    setQuestions(newQuestions);
  };

  const updateQuestion = (qIndex: number, text: string) => {
    const newQuestions = [...questions];
    newQuestions[qIndex].question = text;
    setQuestions(newQuestions);
  };

  const updateOption = (qIndex: number, oIndex: number, text: string) => {
    const newQuestions = [...questions];
    newQuestions[qIndex].options[oIndex] = text;
    setQuestions(newQuestions);
  };

  const setCorrectAnswer = (qIndex: number, oIndex: number) => {
    const newQuestions = [...questions];
    newQuestions[qIndex].correct_answer_index = oIndex;
    setQuestions(newQuestions);
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    createMutation.mutate({ discount_reward: discount, questions });
  };

  if (isLoading) {
    return <div className="flex justify-center p-8"><Loader2 className="w-6 h-6 animate-spin text-indigo-400" /></div>;
  }

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <h4 className="text-white font-medium">Активные и прошедшие квизы</h4>
        {!isCreating && (
          <button
            onClick={() => setIsCreating(true)}
            className="px-4 py-2 bg-indigo-500 hover:bg-indigo-600 text-white rounded-xl text-sm font-medium transition flex items-center"
          >
            <Plus className="w-4 h-4 mr-2" />
            Создать новый квиз
          </button>
        )}
      </div>

      {isCreating && (
        <form onSubmit={handleSubmit} className="bg-white/[0.03] border border-indigo-500/30 rounded-2xl p-6 space-y-6">
          <div className="flex justify-between items-center mb-4">
            <h5 className="text-indigo-400 font-bold flex items-center">
              <PlayCircle className="w-4 h-4 mr-2" />
              Новый Квиз
            </h5>
            <button
              type="button"
              onClick={() => setIsCreating(false)}
              className="text-slate-400 hover:text-white"
            >
              Отмена
            </button>
          </div>

          <div>
            <label className="block text-sm text-slate-400 mb-1">Размер скидки (%)</label>
            <input
              type="number"
              value={discount}
              onChange={e => setDiscount(Number(e.target.value))}
              className="w-full bg-black/40 border border-white/10 rounded-xl px-4 py-2 text-white focus:outline-none focus:border-indigo-500"
              min="1" max="100"
            />
          </div>

          <div className="space-y-6">
            {questions.map((q, qIndex) => (
              <div key={qIndex} className="bg-black/20 p-4 rounded-xl border border-white/5 space-y-4">
                <div className="flex justify-between">
                  <span className="text-slate-300 font-medium">Вопрос {qIndex + 1}</span>
                  {questions.length > 1 && (
                    <button type="button" onClick={() => removeQuestion(qIndex)} className="text-red-400 hover:text-red-300">
                      <Trash2 className="w-4 h-4" />
                    </button>
                  )}
                </div>

                <input
                  type="text"
                  placeholder="Текст вопроса..."
                  value={q.question}
                  onChange={e => updateQuestion(qIndex, e.target.value)}
                  className="w-full bg-black/40 border border-white/10 rounded-xl px-4 py-2 text-white focus:outline-none focus:border-indigo-500"
                  required
                />

                <div className="space-y-2 pl-4 border-l-2 border-indigo-500/20">
                  {q.options.map((opt, oIndex) => (
                    <div key={oIndex} className="flex items-center space-x-2">
                      <input
                        type="radio"
                        name={`correct-${qIndex}`}
                        checked={q.correct_answer_index === oIndex}
                        onChange={() => setCorrectAnswer(qIndex, oIndex)}
                        className="w-4 h-4 text-indigo-500 bg-black/40 border-white/20 focus:ring-indigo-500"
                        title="Отметить как правильный ответ"
                      />
                      <input
                        type="text"
                        placeholder={`Вариант ${oIndex + 1}`}
                        value={opt}
                        onChange={e => updateOption(qIndex, oIndex, e.target.value)}
                        className="flex-1 bg-black/40 border border-white/10 rounded-lg px-3 py-1.5 text-sm text-white focus:outline-none focus:border-indigo-500"
                        required
                      />
                      {q.options.length > 2 && (
                        <button type="button" onClick={() => removeOption(qIndex, oIndex)} className="text-slate-500 hover:text-red-400 p-1">
                          <Trash2 className="w-4 h-4" />
                        </button>
                      )}
                    </div>
                  ))}
                  <button
                    type="button"
                    onClick={() => addOption(qIndex)}
                    className="text-xs text-indigo-400 hover:text-indigo-300 mt-2"
                  >
                    + Добавить вариант
                  </button>
                </div>
              </div>
            ))}
          </div>

          <div className="flex gap-4">
            <button
              type="button"
              onClick={addQuestion}
              className="flex-1 py-2 bg-white/5 hover:bg-white/10 text-white rounded-xl text-sm font-medium transition border border-white/10"
            >
              Добавить вопрос
            </button>
            <button
              type="submit"
              disabled={createMutation.isPending}
              className="flex-1 py-2 bg-indigo-500 hover:bg-indigo-600 text-white rounded-xl text-sm font-medium transition flex items-center justify-center"
            >
              {createMutation.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <><Save className="w-4 h-4 mr-2" /> Запустить Квиз</>}
            </button>
          </div>
        </form>
      )}

      {/* List */}
      <div className="grid gap-3">
        {quizzes?.map(quiz => (
          <div key={quiz.id} className="bg-white/[0.02] border border-white/5 p-4 rounded-xl flex items-center justify-between">
            <div>
              <div className="text-white font-medium">Квиз #{quiz.id}</div>
              <div className="text-xs text-slate-400 mt-1">
                Награда: {quiz.discount_reward}% скидки · Создан: {new Date(quiz.created_at).toLocaleDateString()}
              </div>
            </div>
            <div className="flex items-center gap-4">
              <span className={`px-2 py-1 rounded-md text-xs font-medium ${quiz.is_active ? 'bg-emerald-500/20 text-emerald-400' : 'bg-slate-500/20 text-slate-400'}`}>
                {quiz.is_active ? 'Активен' : 'Завершён'}
              </span>
              {quiz.is_active && (
                <button
                  onClick={() => {
                    if (confirm('Остановить этот квиз?')) {
                      deleteMutation.mutate(quiz.id);
                    }
                  }}
                  className="text-red-400 hover:text-red-300 p-2 bg-red-400/10 rounded-lg"
                  title="Остановить"
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              )}
            </div>
          </div>
        ))}
        {quizzes?.length === 0 && (
          <div className="text-center py-8 text-slate-400 text-sm">
            Нет созданных квизов
          </div>
        )}
      </div>
    </div>
  );
}
