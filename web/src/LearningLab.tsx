import { useState } from 'react';

const lessons = [
  {
    title: '1. Find the silent stage',
    concept: 'Audio tracks and text take different paths through the same room.',
    steps: ['Start a session with the microphone off. Type: “Say hello in one sentence.”', 'Turn on the microphone, say the same sentence, then turn it off. Compare the transcript and reply.'],
    observe: 'If typing works but speaking does not, check microphone permission, publication and recognition first. If text arrives but sound does not, check playback permission, output device and the agent audio track.',
    restore: 'Turn the microphone off when finished. End the session if you need to change providers.',
    question: 'A customer is connected but hears nothing. What evidence would separate a transport issue from a recognition or playback issue?',
  },
  {
    title: '2. Interrupt and recover',
    concept: 'An RPC is a request to the agent. Stopping playback and cancelling generation are separate.',
    steps: ['Ask for a five-sentence explanation of rooms. While it speaks, press Stop reply.', 'Wait for the controls to recover, then type: “What is a track? One sentence.”'],
    observe: 'Listen for audio to stop and confirm the next reply succeeds. A button changing state alone is not proof of cancellation. Slow drain is different from a disconnected room.',
    restore: 'If controls stay blocked, end the session and check Studio status. Do not repeatedly restart an uncertain model process.',
    question: 'How would you show that interruption released resources and did not merely hide the audio?',
  },
  {
    title: '3. Break the model connection',
    concept: 'Room transport and the reasoning endpoint are independent components.',
    steps: ['Only in a disposable local setup: end the session and note your working Ollama URL and model. Keep local LiveKit selected.', 'In Settings, select Ollama and replace its URL with http://127.0.0.1:1/v1 (an intentionally unavailable local port). Save, close Settings, then try Start session once.'],
    observe: 'Saving checks the format; starting the session should reject the unavailable endpoint with an actionable error. Do not expect a room disconnect: the failure happens before a new conversation starts. No cloud fallback should occur.',
    restore: 'Restore the exact previous URL and model, save, then start a session and send one short message. Do not stop a shared Ollama or LiveKit service.',
    question: 'Why is an HTTP endpoint failure different from a WebRTC connection failure, and what would you check first?',
  },
];

export function LearningLab() {
  const [selected, setSelected] = useState(0);
  const [observed, setObserved] = useState<number[]>([]);
  const lesson = lessons[selected];
  return <details className="learn-lesson learning-lab">
    <summary>Practice troubleshooting</summary>
    <p>Three guided exercises using Studio’s real controls. Opening this guide changes nothing. Use non-private test sentences and check your selected services first.</p>
    <label htmlFor="learning-exercise">Choose an exercise</label>
    <select id="learning-exercise" value={selected} onChange={event => setSelected(Number(event.target.value))}>
      {lessons.map((item, index) => <option key={item.title} value={index}>{item.title}</option>)}
    </select>
    <h3>{lesson.title}</h3>
    <p>{lesson.concept}</p>
    <ol>{lesson.steps.map(step => <li key={step}>{step}</li>)}</ol>
    <h4>What to look for</h4><p>{lesson.observe}</p>
    <h4>Get back to working</h4><p>{lesson.restore}</p>
    <h4>Explain it aloud</h4><p>{lesson.question}</p>
    <label className="lab-observed"><input type="checkbox" checked={observed.includes(selected)} onChange={event => setObserved(event.target.checked ? [...observed, selected] : observed.filter(item => item !== selected))} />I tried this and checked recovery</label>
    <p role="status">{observed.length} of 3 self-reported. This is your checklist, not an automated test result. Progress lasts while this view stays open.</p>
    <a href="https://github.com/c-mongan/livekit-voice-studio/blob/main/docs/learn-livekit.md" target="_blank" rel="noreferrer">Full learning and interview guide ↗</a>
  </details>;
}
