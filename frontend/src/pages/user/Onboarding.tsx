import OnboardingQuiz from '../../components/OnboardingQuiz';
import { useAuthSelector } from '../../context/AuthContext';

interface OnboardingProps {
  welcomeQuizEnabled?: boolean;
  onCompleted: () => void | Promise<void>;
}

export default function Onboarding({ welcomeQuizEnabled = false, onCompleted }: OnboardingProps) {
  const user = useAuthSelector((state) => state.user);

  return (
    <div className="min-h-[74vh] w-full py-4">
      <OnboardingQuiz
        userId={user?.telegram_id}
        welcomeQuizEnabled={welcomeQuizEnabled}
        onCompleted={onCompleted}
      />
    </div>
  );
}
