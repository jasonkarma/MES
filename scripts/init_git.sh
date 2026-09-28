#!/bin/bash
# Git initialization and first commit script

echo "=========================================="
echo "Git Repository Initialization"
echo "=========================================="
echo ""

# Check if git is installed
if ! command -v git &> /dev/null; then
    echo "Error: Git is not installed. Please install Git first."
    exit 1
fi

# Initialize repository if not already initialized
if [ ! -d ".git" ]; then
    echo "Initializing Git repository..."
    git init
    echo "✓ Repository initialized"
else
    echo "✓ Git repository already exists"
fi

echo ""
echo "Checking .gitignore..."
if [ -f ".gitignore" ]; then
    echo "✓ .gitignore exists"
else
    echo "⚠ Warning: .gitignore not found"
fi

echo ""
echo "Staging files..."
git add .

echo ""
echo "Creating initial commit..."
git commit -m "Initial commit: GenAI-powered MES defect triage system

Features:
- Odoo XML-RPC integration
- GPT-4 classification with function calling
- RAG system for SOP/manual retrieval
- Automated maintenance ticket creation
- Edge case handling (ambiguity, hallucination, missing data)
- Comprehensive test scenarios
- Complete documentation

Assignment: Part A - Integrate GenAI into Live Process
"

echo ""
echo "✓ Initial commit created"

echo ""
echo "=========================================="
echo "Git Setup Complete"
echo "=========================================="
echo ""
echo "Next steps:"
echo "  1. Create GitHub repository"
echo "  2. Add remote: git remote add origin <your-repo-url>"
echo "  3. Push: git push -u origin main"
echo ""
echo "To view status: git status"
echo "To view log: git log --oneline"
echo ""
