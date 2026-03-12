import { createOpenAICompatible } from '@ai-sdk/openai-compatible';

// Provider wrapper for DeepSeek's OpenAI-compatible API
export const deepseek = createOpenAICompatible({
  name: 'deepseek',
  baseURL: 'https://api.deepseek.com/v1',
  apiKey: process.env.DEEPSEEK_API_KEY!,
});

// Fast general-purpose chat model
export const ds_chat = deepseek('deepseek-chat');

// Reasoning model for deeper chain-of-thought tasks
export const ds_reason = deepseek('deepseek-reasoner');
