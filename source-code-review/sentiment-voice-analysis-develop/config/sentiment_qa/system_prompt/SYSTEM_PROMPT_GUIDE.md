# System Prompt Design Guide

A comprehensive guide for creating effective system prompts for LLM-based applications, with examples from a Call Center QA system.

## Table of Contents

1. [Core Structure](#core-structure)
2. [Essential Components](#essential-components)
3. [Best Practices](#best-practices)
4. [Complete Example](#complete-example)
5. [Common Patterns](#common-patterns)

---

## Core Structure

A well-designed system prompt should follow this hierarchical structure:

```
1. Role & Identity
2. Objective
3. Expected Input Format
4. Context & Knowledge Base
5. Steps & Reasoning
6. Rules & Constraints
7. Output Format
```

### Why This Order?

- **IdentityFirst**: Establishes the perspective and expertise level
- **Objective**: Defines what success looks like
- **Input/Output**: Sets clear boundaries for data flow
- **Context**: Provides domain knowledge
- **Steps**: Guides the reasoning process
- **Rules**: Prevents common errors

---

## Essential Components

### 1. Role & Identity

**Purpose**: Define who the AI is, their expertise, and communication style.

**Template**:

```markdown
## 1. Role & Identity

**Role:** [Specific job title or function]
**Tone:** [Communication style: analytical, friendly, formal, etc.]
**Expertise:**

- [Domain expertise area 1]
- [Domain expertise area 2]
- [Domain expertise area 3]
```

**** (Call Center QA):

```markdown
## 1. Role & Identity

**Role:** You are a Senior Call Center Quality Assurance Analyst specializing in telecommunications outbound telesale operations.
**Tone:** Analytical, objective, and detail-oriented with a focus on compliance and quality standards.
**Expertise:**

- Call center quality assessment and COPC standards
- Telecommunications telesale operations and outbound call evaluation
- Thai language conversation analysis and sentiment interpretation
- Agent performance evaluation across professionalism, compliance, and customer experience dimensions
```

---

### 2. Objective

**Purpose**: Clearly state the main goal and what the AI should accomplish.

**Template**:

```markdown
## 2. Objective

**Goal:** [Main task description in one clear sentence]

**Scope**: [What IS included and what IS NOT included]

**Success Criteria**: [How to measure if the goal is achieved]
```

**Example**:

```markdown
## 2. Objective

**Goal:** Analyze outbound telesale call transcripts between agents and customers in the telecommunications industry. Evaluate agent performance across 4 quality dimensions: 1) Operations and Professionalism, 2) Sales Effectiveness, 3) Customer Experience (CX), and 4) Compliance. Provide structured boolean assessments for each quality criterion.

**Scope**:

- IN: Agent performance evaluation based on actual conversation content
- OUT: Customer behavior evaluation, call outcome prediction, sales recommendations

**Success Criteria**: Accurate boolean (True/False) assessment for each defined quality criterion with evidence from the transcript.
```

---

### 3. Expected Input Format

**Purpose**: Define exactly what data the AI will receive and in what format.

**Template**:

```markdown
## 3. Expected Input Format

You will receive [data type] in the following format:

[Show exact format with examples]

**Important Context:**

- [Key context point 1]
- [Key context point 2]
```

**Example**:

````markdown
## 3. Expected Input Format

You will receive a **call transcript** from an **outbound telesale conversation** between a telecommunications **Agent** and a **Customer** in Thai language, formatted as:

```text
Agent: [Agent's dialogue in Thai]
Customer: [Customer's response in Thai]
Agent: [Agent's dialogue in Thai]
Customer: [Customer's response in Thai]
...
```
````

**Important Context:**

- All calls are **OUTBOUND only** (agent initiates the call to customer)
- Primary language is **Thai** (ภาษาไทย)
- Industry: Telecommunications (True, Dtac service providers)
- Purpose: Telesale/promotional offers/customer retention

````

---

### 4. Context & Knowledge Base

**Purpose**: Provide domain-specific knowledge, frameworks, criteria, and reference data.

**Structure Options**:

**Option A: Hierarchical Categories** (Best for complex evaluation frameworks)
```markdown
## 4. Context & Knowledge Base

### Evaluation Framework

#### **Main Category 1: [Category Name]**
Brief description of what this category evaluates.

##### **Sub-Category 1.1: [Sub-category Name]** ([Thai/Local Name])
Description of this sub-category.

###### **1.1.1 criterion_name** - Criterion Display Name ([Thai Name])
- **Definition:** Clear explanation of what this criterion measures
- **True when:** Specific conditions that make this True
- **False when:** Specific conditions that make this False
- **Thai keywords (positive):** คำสำคัญที่บ่งบอกพฤติกรรมดี
- **Thai keywords (negative):** คำสำคัญที่บ่งบอกพฤติกรรมไม่ดี
- **Positive example:** ตัวอย่างสถานการณ์ที่ผ่านเกณฑ์
- **Negative example:** ตัวอย่างสถานการณ์ที่ไม่ผ่านเกณฑ์
````

**Option B: Simple Reference Data** (Best for knowledge bases)

```markdown
## 4. Context & Knowledge Base

### Company Information

- **Company Names:** [List of companies]
- **Products:** [List of products]
- **Services:** [List of services]

### Industry Standards

- **Standard 1:** [Description]
- **Standard 2:** [Description]

### Common Scenarios

**Scenario Type 1:**

- Description: [What it is]
- Expected behavior: [How to handle]
```

---

### 5. Steps & Reasoning

**Purpose**: Guide the AI through a logical, step-by-step analysis process.

**Template**:

```markdown
## 5. Steps & Reasoning

Follow these steps sequentially to [accomplish the objective]:

1. **[Step 1 Name]** - [Brief description of what to do]
2. **[Step 2 Name]** - [Brief description of what to do]
3. **[Step 3 Name]** - [Brief description of what to do]
   ...
   N. **[Final Step]** - [How to structure/deliver the output]
```

**Example**:

```markdown
## 5. Steps & Reasoning

Follow these steps sequentially to analyze the call transcript:

1. **Read the entire transcript** from opening to closing to understand the complete conversation flow
2. **Identify the call opening section** - evaluate agent identification, call origin disclosure, and consent request
3. **Assess language and tone throughout** - detect inappropriate language, clarity issues, or pacing problems
4. **Analyze active listening behaviors** - identify probing questions and needs assessment depth
5. **Evaluate the call closing** - check for resolution confirmation, courtesy, and smooth ending
6. **Evaluate sales effectiveness** - assess needs analysis, offer presentation, objection handling, and closing attempts
7. **Evaluate customer experience** - assess empathy, communication clarity, and trust building
8. **Verify compliance** - check data privacy, sales transparency, and professional conduct
9. **Map observations to boolean criteria** - for each sub-criterion, determine True (compliant) or False (non-compliant)
10. **Structure the output** according to the JSON schema
```

---

### 6. Rules & Constraints

**Purpose**: Set boundaries, prevent errors, and clarify edge cases.

**Template**:

```markdown
## 6. Rules & Constraints

### General Rules

- [Rule 1]
- [Rule 2]

### Edge Case Handling

- **If [condition]:** [What to do]
- **If [condition]:** [What to do]

### What NOT to Do

- **DON'T** [Common mistake 1]
- **DON'T** [Common mistake 2]
```

**Example**:

```markdown
## 6. Rules & Constraints

### General Rules

- Evaluate based on **actual conversation content**, not assumptions
- Boolean outputs must be **True** (criterion met/compliant) or **False** (criterion not met/non-compliant)
- **Thai language keywords** are provided as detection signals - use them to identify behaviors
- Consider both **positive indicators** (presence of good behaviors) and **negative indicators** (presence of violations)
- If a criterion cannot be evaluated due to insufficient information, default to **False**

### Focus & Scope

- Focus on **agent behavior only** - customer behavior is context, not evaluation target
- Each sub-criterion is **independent** - evaluate separately
- Do not conflate multiple criteria - assess each boolean field precisely

### Context-Specific Rules

- **Outbound context:** Agent initiates contact, so call opening requirements are stricter
- **Thai language:** Pay attention to tone, politeness markers (ครับ/ค่ะ), and cultural nuances

### What NOT to Do

- **DON'T** give partial scores or percentages - only True/False
- **DON'T** make assumptions about missing parts of conversation
- **DON'T** evaluate customer behavior - only agent performance
```

---

### 7. Output Format

**Purpose**: Define exact structure, format, and schema for the AI's response.

**Template**:

```markdown
## 7. Output Format

**Format:** [JSON, Markdown, Plain text, etc.]
**Structure/Schema:**

[Show exact schema with data types]

**Example Output:**

[Show 1-2 realistic examples]

**Remember:**

- [Important note 1]
- [Important note 2]
```

**Example**:

````markdown
## 7. Output Format

**Format:** JSON

**Structure/Schema:**

```json
{
  "category_1": {
    "sub_category_1_1": {
      "criterion_1": Boolean,
      "criterion_2": Boolean
    },
    "sub_category_1_2": {
      "criterion_3": Boolean
    }
  },
  "category_2": {
    "criterion_4": Boolean,
    "criterion_5": Boolean
  }
}
```
````

**Example Output:**

```json
{
  "call_opening": {
    "proper_identification": true,
    "call_origin_disclosure": true,
    "consent_before_engagement": false
  },
  "language_and_tone": {
    "behavioral_violation": true,
    "clarity": true,
    "delivery_pace": false
  }
}
```

**Remember:**

- All fields must be present (no optional fields)
- Values are strictly boolean (true/false, lowercase)
- Ensure valid JSON syntax

````

---

## Best Practices

### Language & Clarity

✅ **DO:**
- Use clear, specific language
- Define technical terms
- Provide concrete examples
- Use bullet points and hierarchy
- Bold important terms

❌ **DON'T:**
- Use vague words like "good", "appropriate", "suitable" without definition
- Assume domain knowledge
- Mix multiple instructions in one sentence
- Use ambiguous pronouns

### Structure & Organization

✅ **DO:**
- Follow hierarchical numbering (1, 1.1, 1.1.1)
- Group related items together
- Use consistent formatting
- Separate sections with clear headers
- Provide table of contents for long prompts

❌ **DON'T:**
- Jump between topics randomly
- Bury important rules deep in text
- Mix different levels of abstraction

### Examples & Edge Cases

✅ **DO:**
- Provide both positive and negative examples
- Show edge cases and how to handle them
- Use realistic, domain-specific examples
- Include examples in native language if applicable

❌ **DON'T:**
- Rely only on theoretical descriptions
- Use overly simple or unrealistic examples
- Ignore common edge cases

### Multilingual Prompts

When working with non-English content:

✅ **DO:**
- **Main language: English** for structure, rules, definitions
- **Secondary language: Native** for examples, keywords, specific phrases
- Clearly label which language to use where
- Provide translation/explanation when needed

**Example**:
```markdown
#### **1.1.1 proper_identification** - Proper Identification (การแสดงตัวตนผู้ติดต่อ)
- **Definition:** [English definition]
- **True when:** [English condition]
- **Thai keywords:** สวัสดีครับ, ดิฉันชื่อ, รหัสพนักงาน
- **Positive example:** "สวัสดีครับ ดิฉันชื่อ... รหัสพนักงาน..." [Thai example]
````

---

## Common Patterns

### Pattern 1: Classification Tasks

```markdown
## Role

You are a [specific classifier type] specializing in [domain].

## Objective

Classify [input type] into [N categories/labels] based on [criteria].

## Input Format

[Exact format specification]

## Classification Criteria

### Category 1: [Name]

- **Definition:** [What qualifies]
- **Examples:** [Examples]

### Category 2: [Name]

- **Definition:** [What qualifies]
- **Examples:** [Examples]

## Output Format

{
"category": "[category name]",
"confidence": [0.0-1.0],
"reasoning": "[brief explanation]"
}
```

### Pattern 2: Evaluation/Scoring Tasks

```markdown
## Role

You are a [evaluator type] assessing [what to evaluate].

## Objective

Evaluate [target] across [N dimensions] using [scoring method].

## Evaluation Framework

### Dimension 1: [Name]

- **Criteria:** [What to look for]
- **Score 1:** [When to assign]
- **Score 2:** [When to assign]

## Scoring Rules

- [Rule 1]
- [Rule 2]

## Output Format

{
"dimension_1": score,
"dimension_2": score,
"overall_score": calculated_score
}
```

### Pattern 3: Extraction Tasks

```markdown
## Role

You are a [extractor type] specializing in [domain].

## Objective

Extract [specific information types] from [input type].

## What to Extract

1. **[Field 1 Name]**
   - Definition: [What it is]
   - Format: [Expected format]
   - Example: [Example value]

2. **[Field 2 Name]**
   - Definition: [What it is]
   - Format: [Expected format]
   - Example: [Example value]

## Extraction Rules

- [Rule 1]
- [Edge case handling]

## Output Format

{
"field_1": "extracted value",
"field_2": "extracted value"
}
```

---

## Complete Example: Call Center QA System

See `common_prompt.txt` in this directory for a complete, production-ready example of a comprehensive system prompt for:

- Multi-category evaluation (4 main categories)
- Hierarchical criteria structure (40+ individual boolean criteria)
- Multilingual content (English structure + Thai examples)
- Complex output schema with nested JSON

---

## Tips for Testing & Iteration

1. **Start Simple**: Begin with basic structure, add complexity gradually
2. **Test Edge Cases**: Try inputs that might break your criteria
3. **Iterate Based on Errors**: When AI misunderstands, add clarification to prompt
4. **Version Control**: Keep track of prompt versions and their performance
5. **A/B Testing**: Compare different phrasings for same instruction
6. **Get Feedback**: Have domain experts review criteria and examples

---

## Common Mistakes to Avoid

### 1. Ambiguous Criteria

❌ **Bad**: "Agent should be professional"
✅ **Good**: "Agent maintains polite tone (uses ครับ/ค่ะ), doesn't interrupt customer, and uses formal language"

### 2. Missing Edge Cases

❌ **Bad**: "Evaluate if agent offers product"
✅ **Good**: "Evaluate if agent offers product. If call ends early due to customer hang-up before agent can offer, return False"

### 3. Conflicting Rules

❌ **Bad**:

- "Always return True if X happens"
- "Return False when Y happens"
- [X and Y can happen together]

✅ **Good**: Define precedence rules

### 4. Overloading Single Criterion

❌ **Bad**: "good_customer_service" (too broad)
✅ **Good**: Separate into: "empathy", "clarity", "responsiveness", "resolution"

---

## Prompt Optimization Checklist

Before deploying your system prompt:

- [ ] Clear role and expertise defined
- [ ] Objective stated in one sentence
- [ ] Input format explicitly shown with examples
- [ ] All evaluation criteria have clear True/False conditions
- [ ] Examples provided for each criterion (positive + negative)
- [ ] Edge cases documented
- [ ] Output format shown with schema and example
- [ ] Rules and constraints clearly listed
- [ ] No ambiguous terms without definition
- [ ] Consistent formatting and structure
- [ ] Tested with real sample inputs
- [ ] Validated output against expected results

---

## Additional Resources

- View `common_prompt.yml` for the source knowledge base
- View `common_prompt.txt` for the complete prompt implementation
- Best practices from OpenAI: https://platform.openai.com/docs/guides/prompt-engineering
- Prompt engineering guide: https://www.promptingguide.ai/

---

**Last Updated**: 2026-02-02
**Version**: 1.0
**Maintained By**: QA Team
