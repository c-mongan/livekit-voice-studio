import { createRoot } from 'react-dom/client';
import App from './App';
import './styles.css';

// Session creation is a user action, never a mount effect.
createRoot(document.getElementById('root')!).render(<App />);
