import WebMessenger from '../features/chat/WebMessenger';

interface WebBotChatProps {
  active?: boolean;
}

export default function WebBotChat({ active = true }: WebBotChatProps) {
  return <WebMessenger active={active} />;
}
