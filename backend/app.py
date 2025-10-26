import base64
from flask import Flask, jsonify, request
from flask_cors import CORS
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
import os
import base64
import requests
import json
import time 
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app) 

SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

GEMINI_MODEL_ID = "gemini-2.5-flash" 
GEMINI_API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL_ID}:generateContent"


def get_gmail_service():
    """
    Authenticate and return Gmail API service using the Bearer token
    provided by the client (browser extension).
    """
    auth_header = request.headers.get('Authorization')
    if not auth_header:
        raise Exception('Authorization header is missing')

    try:
        token_type, access_token = auth_header.split(' ')
        if token_type.lower() != 'bearer':
            raise Exception('Authorization header must start with Bearer')
        
        creds = Credentials(access_token, scopes=SCOPES)
        
        return build('gmail', 'v1', credentials=creds)

    except Exception as e:
        print(f"Error parsing auth header: {e}")
        raise Exception(f'Invalid Authorization header: {e}')


def get_email_body(payload):
    """Extract email body from message payload"""
    if 'parts' in payload:
        for part in payload['parts']:
            if part['mimeType'] == 'text/plain':
                data = part['body'].get('data', '')
                if data:
                    return base64.urlsafe_b64decode(data).decode('utf-8')
    else:
        data = payload.get('body', {}).get('data', '')
        if data:
            return base64.urlsafe_b64decode(data).decode('utf-8')
    return ""

def rule_based_analysis(subject, sender, body):
    """Fallback rule-based analysis if Gemini fails or if email is pre-filtered."""
    priority = 5
    reason = "Default priority (AI analysis failed or quota exceeded)"
    
    sender_lower = sender.lower()
    

    low_priority_keywords = ['noreply', 'marketing', 'promo', 'unsubscribe', 'coupon', 'newsletter', 
                             'advertisement', 'deal', 'offer', 'weekly digest', 'sale', 'save now']
    
    if any(word in sender_lower for word in low_priority_keywords) or any(word in subject.lower() for word in low_priority_keywords):
        priority = 2
        reason = "Automated/promotional email filter hit"
        return {
            "summary": f"Email from {sender}: {subject}",
            "priority": priority,
            "reason": reason
        }

    if any(word in sender_lower for word in ['gov', 'government', 'irs', 'court', 'legal', '.gov']):
        priority = 9
        reason = "Government/legal sender"
    elif any(word in sender_lower for word in ['boss', 'manager', 'ceo', 'director']):
        priority = 8
        reason = "Management communication"
    elif any(word in sender_lower for word in ['teacher', 'professor', 'instructor', '.edu']):
        priority = 7
        reason = "Educational authority"
    
    subject_lower = subject.lower()
    urgent_keywords = ['urgent', 'immediate', 'action required', 'deadline', 'asap', 'emergency', 'important']
    if any(word in subject_lower for word in urgent_keywords):
        priority = min(priority + 2, 10)
        reason = f"{reason} + urgent keywords"
    
    body_lower = body[:200].lower()
    if any(word in body_lower for word in ['due date', 'overdue', 'payment', 'suspended', 'expires']):
        priority = min(priority + 1, 10)
    
    summary = f"Email from {sender}: {subject}"
    if len(summary) > 100:
        summary = summary[:97] + "..."
    
    return {
        "summary": summary,
        "priority": priority,
        "reason": reason
    }


def analyze_email_with_gemini(subject, sender, body, date, max_retries=3):
    """Use Google Gemini to analyze and prioritize email with retry logic and pre-check filter."""
    
    low_priority_keywords = ['unsubscribe', 'coupon', 'newsletter', 'advertisement', 'promo', 'marketing', 
                             'noreply', 'deal', 'offer', 'weekly digest', 'sale', 'save now']
    
    sender_lower = sender.lower()
    subject_lower = subject.lower()

    if any(word in sender_lower for word in low_priority_keywords) or any(word in subject_lower for word in low_priority_keywords):
        print(f"Skipping AI analysis for potential promo/spam: {subject}")
        return rule_based_analysis(subject, sender, body)
    
    if not GEMINI_API_KEY:
        print("Falling back to rule-based analysis: GEMINI_API_KEY not found.")
        return rule_based_analysis(subject, sender, body)
    
    
    system_instruction = (
        "You are an email prioritization expert. Your primary goal is to identify emails "
        "that require a *personal, timely response* or contain *critical personal or professional information*. "
        "Be extremely critical of any email that resembles marketing, automated reports, "
        "or social media notifications. Only assign a priority score of 7 or higher if the email "
        "demands immediate human action or contains legally/financially important content."
    )
    
    prompt = f"""{system_instruction}

--- END OF INSTRUCTIONS ---

Analyze this email and provide a brief summary, priority score, and reason.

Email Details:
From: {sender}
Date: {date}
Subject: {subject}
Body: {body[:1000]}

Respond ONLY with valid JSON in this exact format:
{{
    "summary": "brief 1-2 sentence summary",
    "priority": 8,
    "reason": "explanation for priority score"
}}
"""

    for attempt in range(max_retries):
        try:
            response = requests.post(
                f"{GEMINI_API_URL}?key={GEMINI_API_KEY}",
                json={
                    "contents": [
                        {
                            "role": "user",
                            "parts": [{"text": prompt}]
                        }
                    ],
                    "generationConfig": {
                        "responseMimeType": "application/json",
                        "temperature": 0.4,
                        "maxOutputTokens": 1024 
                    }
                },
                timeout=30
            )
            
            if response.status_code == 200:
                result = response.json()
                
                candidate = result.get('candidates', [{}])[0]
                content = candidate.get('content', {})
                parts = content.get('parts', [{}])
                text = parts[0].get('text')
                
                if not text:
                    print(f"Gemini returned empty or non-text content: {result}")
                    return rule_based_analysis(subject, sender, body)

                try:
                     analysis = json.loads(text)
                except json.JSONDecodeError:
                    print(f"Gemini returned non-JSON text: {text}")
                    return rule_based_analysis(subject, sender, body)
                
                # Ensure priority is a sane value
                analysis['priority'] = max(1, min(10, int(analysis.get('priority', 5))))
                
                return analysis

            elif response.status_code == 429:
                print(f"Quota Exceeded (429). Retrying in {2**attempt} seconds... (Attempt {attempt+1}/{max_retries})")
                time.sleep(2**attempt)
                continue 

            else:
                print(f"Gemini API external error: {response.status_code} - {response.text}")
                return rule_based_analysis(subject, sender, body)
                
        except Exception as e:
            print(f"Error with Gemini API call (network/timeout): {e}")
            if attempt < max_retries - 1:
                 time.sleep(2**attempt)
                 continue
            return rule_based_analysis(subject, sender, body)

    return rule_based_analysis(subject, sender, body)


@app.route('/api/unread-emails', methods=['GET'])
def get_unread_emails():
    """Fetch and analyze unread emails"""
    try:
        service = get_gmail_service()
        
        results = service.users().messages().list(
            userId='me',
            q='is:unread',
            maxResults=10  
        ).execute()
        
        messages = results.get('messages', [])
        
        if not messages:
            return jsonify({'emails': []})
        
        analyzed_emails = []
        
        for msg in messages:
            try:
                message = service.users().messages().get(
                    userId='me',
                    id=msg['id'],
                    format='full'
                ).execute()
                
                headers = message['payload']['headers']
                subject = next((h['value'] for h in headers if h['name'] == 'Subject'), 'No Subject')
                sender = next((h['value'] for h in headers if h['name'] == 'From'), 'Unknown')
                date = next((h['value'] for h in headers if h['name'] == 'Date'), '')
                
                body = get_email_body(message['payload'])
                
                analysis = analyze_email_with_gemini(subject, sender, body, date)
                
                analyzed_emails.append({
                    'id': msg['id'],
                    'subject': subject,
                    'sender': sender,
                    'date': date,
                    'summary': analysis['summary'],
                    'priority': analysis['priority'],
                    'reason': analysis['reason']
                })
                
            except Exception as e:
                print(f"Error processing email {msg['id']}: {e}")
                continue
        
        analyzed_emails.sort(key=lambda x: x['priority'], reverse=True)
        
        return jsonify({'emails': analyzed_emails})
    
    except Exception as e:
        if 'Authorization' in str(e):
            return jsonify({'error': str(e)}), 401
        return jsonify({'error': str(e)}), 500

@app.route('/api/mark-read', methods=['POST'])
def mark_as_read():
    """Mark an email as read"""
    try:
        service = get_gmail_service()
        data = request.json
        email_id = data.get('email_id')
        
        if not email_id:
            return jsonify({'error': 'email_id is required'}), 400
            
        service.users().messages().modify(
            userId='me',
            id=email_id,
            body={'removeLabelIds': ['UNREAD']}
        ).execute()
        
        return jsonify({'success': True})
    
    except Exception as e:
        if 'Authorization' in str(e):
            return jsonify({'error': str(e)}), 401
        return jsonify({'error': str(e)}), 500

@app.route('/api/ask-question', methods=['POST'])
def ask_question():
    """Ask questions about emails using Gemini"""
    try:
        data = request.json
        question = data.get('question')
        emails_context = data.get('emails', [])
        
        if not question:
            return jsonify({'error': 'No question provided'}), 400
        
        if not GEMINI_API_KEY:
            return jsonify({'error': 'Gemini API key not configured on server'}), 500
        
        context = "Here are the user's current unread emails:\n\n"
        for i, email in enumerate(emails_context, 1):
            context += f"Email {i}:\n"
            context += f"From: {email.get('sender', 'Unknown')}\n"
            context += f"Subject: {email.get('subject', 'No subject')}\n"
            context += f"Summary: {email.get('summary', 'No summary')}\n"
            context += f"Priority: {email.get('priority', 'N/A')}/10\n\n"
        
        prompt = f"""{context}

User's question: {question}

Please answer the user's question about their emails. Be helpful, concise, and specific. Reference specific emails by their sender or subject when relevant."""

        response = requests.post(
            f"{GEMINI_API_URL}?key={GEMINI_API_KEY}",
            json={
                "contents": [{
                    "parts": [{"text": prompt}]
                }],
                "generationConfig": { 
                    "temperature": 0.7,
                    "maxOutputTokens": 500
                }
            },
            timeout=30
        )
        
        if response.status_code == 200:
            result = response.json()

            candidate = result.get('candidates', [{}])[0]
            content = candidate.get('content', {})
            parts = content.get('parts', [{}])
            answer = parts[0].get('text')
            
            if candidate.get('finishReason') == 'SAFETY':
                 return jsonify({'error': 'Your query was blocked by AI safety settings.'}), 400

            if not answer:
                print(f"Gemini API returned empty data: {result}")
                return jsonify({'error': 'Failed to get valid response from Gemini'}), 500
                
            return jsonify({'answer': answer})
        else:
            print(f"Gemini API Question Error: Status {response.status_code}, Response: {response.text}")
            return jsonify({'error': f'Gemini API request failed. Status {response.status_code}. Possible invalid key or usage limit reached.'}), 500
            
    except Exception as e:
        print(f"Internal Server Error during AI query: {str(e)}")
        return jsonify({'error': f'Internal Server Error during AI query: {str(e)}'}), 500


@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'ok',
        'ai_provider': 'Google Gemini 2.5 Flash',
        'api_key_configured': bool(GEMINI_API_KEY),
        'gemini_key_prefix': GEMINI_API_KEY[:4] + '...' if GEMINI_API_KEY else None
    })

@app.route('/')
def first_check():
    return "Backend is running."

if __name__ == '__main__':
    print("\nmailman backend")
    print("=" * 50)
    print("gmail API: Ready (expects Bearer token)")
    print("AI Provider: Google Gemini 2.5 Flash")
    
    if GEMINI_API_KEY:
        print(f"Gemini API Key: Configured (Prefix: {GEMINI_API_KEY[:4]}...)")
    else:
        print("Warning: GEMINI_API_KEY not set! Falling back to rule-based analysis.")
    
    print("\nServer starting on http://localhost:5000")
    print("=" * 50 + "\n")
    
    app.run(debug=True, port=5000)