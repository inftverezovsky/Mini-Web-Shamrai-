import OnboardingQuiz from '../../components/OnboardingQuiz';
import { useAuthSelector } from '../../context/AuthContext';

interface OnboardingProps {
  onCompleted: () => void | Promise<void>;
}

export default function Onboarding({ onCompleted }: OnboardingProps) {
  const user = useAuthSelector((state) => state.user);

  return (
    <div className="min-h-[74vh] w-full py-4">
      <OnboardingQuiz
        userId={user?.telegram_id}
        onCompleted={onCompleted}
      />
    </div>
  );
}
