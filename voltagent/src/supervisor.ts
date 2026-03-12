import { Agent, VoltAgent } from '@voltagent/core';
import { honoServer } from '@voltagent/server-hono';
import { ds_chat } from './deepseek';
import {
  narratorAgent,
  failureGuardAgent,
  regimeAgent,
  sentimentAgent,
  caretakerAgent,
} from './agents';

const supervisorAgent = new Agent({
  name: 'SupervisorAgent',
  model: ds_chat,
  instructions: `
You coordinate the FancyFangBot AI council.
You receive trading events and delegate to specialist agents.
You do not trade or analyze directly - you route and coordinate.
`,
  subAgents: [
    narratorAgent,
    failureGuardAgent,
    regimeAgent,
    sentimentAgent,
    caretakerAgent,
  ],
});

new VoltAgent({
  agents: { supervisor: supervisorAgent },
  server: honoServer({ port: 3141 }),
  // observability can be added later via VoltOps provider
});

console.log('VoltAgent supervisor listening on http://localhost:3141');
