#!/bin/bash

# ─────────────────────────────────────────────────────────────
#   AUTOMATIC VOLTAGENT GITHUB ANALYZER SETUP
#   For the github-repo-analyzer subproject
# ─────────────────────────────────────────────────────────────

set -e  # exit on any error

cd github-repo-analyzer

echo "📁 Creating directories if they don't exist..."
mkdir -p src/agents src/tools

echo "📝 Writing GitHub tool (src/tools/github.ts)..."
cat > src/tools/github.ts << 'EOF'
// src/tools/github.ts
export async function getRepoInfo(repoUrl: string) {
  const match = repoUrl.match(/github\.com\/([^\/]+)\/([^\/]+)/);
  if (!match) throw new Error('Invalid GitHub URL. Use format: https://github.com/owner/repo');

  const [, owner, repo] = match;
  const res = await fetch(`https://api.github.com/repos/${owner}/${repo}`);
  if (!res.ok) {
    const error = await res.json();
    throw new Error(`GitHub API error: ${error.message}`);
  }

  const data = await res.json();
  return {
    fullName: data.full_name,
    description: data.description,
    stars: data.stargazers_count,
    forks: data.forks_count,
    language: data.language,
    lastCommit: data.updated_at,
    openIssues: data.open_issues_count,
    license: data.license?.name || 'None',
    createdAt: data.created_at,
  };
}
EOF

echo "📝 Writing agent (src/agents/githubAnalyzer.ts)..."
cat > src/agents/githubAnalyzer.ts << 'EOF'
// src/agents/githubAnalyzer.ts
import { VoltAgent } from '@voltagent/core';  // adjust import if needed
import { getRepoInfo } from '../tools/github';

const agent = new VoltAgent({
  name: 'GitHub Repo Analyzer',
  description: 'I fetch and analyze GitHub repositories.',
  tools: [getRepoInfo],
  systemPrompt: `
You are a helpful GitHub repository analyst. 
When the user provides a GitHub repo URL, use the 'getRepoInfo' tool to fetch its metadata.
Then, based on the data, give a concise summary including:
- Basic info (name, description, language)
- Popularity (stars, forks)
- Activity (last commit, open issues)
- Overall health (is it actively maintained? any red flags?)

Keep it friendly and insightful. If you can't find the repo, explain why.
  `,
});

export default agent;
EOF

echo "📝 Configuring main entry point (src/index.ts)..."
cat > src/index.ts << 'EOF'
import { serve } from '@hono/node-server'
import { Hono } from 'hono'
import agent from './agents/githubAnalyzer'

const app = new Hono()

app.get('/', (c) => c.text('VoltAgent GitHub Analyzer is running!'))

app.post('/api/agent', async (c) => {
  const { message } = await c.req.json()
  const response = await agent.run(message)
  return c.json({ response })
})

app.get('/api/agent', async (c) => {
  const message = c.req.query('message')
  if (!message) return c.text('Missing message parameter', 400)
  const response = await agent.run(message)
  return c.json({ response })
})

serve(app)
console.log('Server running on http://localhost:3000')
EOF

echo "🔑 Setting up .env (you'll need to add your OpenRouter key)..."
if [ ! -f .env ]; then
  cat > .env << 'EOF'
OPENROUTER_API_KEY=your_openrouter_api_key_here
DEFAULT_MODEL=deepseek/deepseek-chat
EOF
  echo "✅ .env created. Edit it to add your actual OpenRouter API key."
else
  echo "⚠️  .env already exists. Skipping."
fi

echo "📦 Installing any missing dependencies (just in case)..."
npm install

echo "🚀 Starting dev server..."
npm run dev
