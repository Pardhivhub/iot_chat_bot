#!/bin/zsh

# Ensure Ollama is running and accessible
export PATH=$PATH:/opt/homebrew/bin
OLLAMA_BIN="/Applications/Ollama.app/Contents/Resources/ollama"

# Configuration for Claude Code to use Ollama
export ANTHROPIC_AUTH_TOKEN="ollama"
export ANTHROPIC_BASE_URL="http://localhost:11434"

# Model to use (qwen2.5-coder:1.5b was downloaded, 7b is recommended for better results)
MODEL="qwen2.5-coder:1.5b"

echo "Starting Claude Code with Ollama backend ($MODEL)..."
./node_modules/.bin/claude --model "$MODEL" "$@"
