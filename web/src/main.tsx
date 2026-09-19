import { createRoot } from 'react-dom/client';
import { initializeAppearance } from './appearance';
import App from './App';
import './styles.css';

initializeAppearance();

// Session creation is a user action, never a mount effect.
createRoot(document.getElementById('root')!).render(<App />);
