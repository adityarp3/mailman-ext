const API_URL = 'http://localhost:5000';

let currentEmails = [];

function getAuthToken() {
  return new Promise((resolve, reject) => {
    chrome.identity.getAuthToken({ interactive: true }, (token) => {
      if (chrome.runtime.lastError || !token) {
        console.error("DEBUG: Token Retrieval Failed!", chrome.runtime.lastError);
        reject(new Error(chrome.runtime.lastError.message || 'Authentication failed. Please verify manifest.json.'));
      } else {
        console.log("DEBUG: Token Retrieved Successfully.");
        resolve(token);
      }
    });
  });
}

async function fetchEmails(token) {
  console.log("DEBUG: fetchEmails called. Token length:", token.length);
  
  const loading = document.getElementById('loading');
  const emailsContainer = document.getElementById('emails');
  const refreshBtn = document.getElementById('refreshBtn');
  const chatSection = document.getElementById('chatSection');
  const chatInput = document.getElementById('chatInput');
  const chatSendBtn = document.getElementById('chatSendBtn');

  loading.style.display = 'block';
  emailsContainer.style.display = 'none';
  refreshBtn.disabled = true;
  chatSection.style.display = 'none';

  try {
    if (!token || token.length < 10) {
        throw new Error("Missing Auth Token. Cannot fetch emails.");
    }
    
    const response = await fetch(`${API_URL}/api/unread-emails`, {
      headers: {
        'Authorization': `Bearer ${token}`
      }
    });
    
    const data = await response.json();

    loading.style.display = 'none';
    emailsContainer.style.display = 'block';
    refreshBtn.style.display = 'block';
    refreshBtn.disabled = false;

    if (data.error) {
      emailsContainer.innerHTML = `
        <div class="error">
          <strong>Error from Backend:</strong> ${data.error}
          <p>This may mean your token expired or the Gemini API key is missing on the server.</p>
        </div>
      `;
      return;
    }

    if (!data.emails || data.emails.length === 0) {
      emailsContainer.innerHTML = `
        <div class="no-emails">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M22 12h-4l-3 9L9 3l-3 9H2"></path>
          </svg>
          <h3>All caught up!</h3>
          <p>No unread emails at the moment.</p>
        </div>
      `;
      return;
    }

    currentEmails = data.emails;

    emailsContainer.innerHTML = data.emails.map(email => {
      const priorityClass = email.priority >= 7 ? 'priority-high' : 
                            email.priority >= 4 ? 'priority-medium' : 
                            'priority-low';
      
      const priorityLabel = email.priority >= 7 ? 'Urgent' : 
                            email.priority >= 4 ? 'Important' : 
                            'Normal';

      return `
        <div class="email-card ${priorityClass}" data-id="${email.id}">
          <span class="priority-badge">
            ${priorityLabel} (${email.priority}/10)
          </span>
          <div class="email-subject">${escapeHtml(email.subject)}</div>
          <div class="email-sender">From: ${escapeHtml(email.sender)}</div>
          <div class="email-summary">${escapeHtml(email.summary)}</div>
          <div class="email-reason">📌 ${escapeHtml(email.reason)}</div>
        </div>
      `;
    }).join('');

    chatSection.style.display = 'block';
    chatInput.disabled = false;
    chatSendBtn.disabled = false;

    document.querySelectorAll('.email-card').forEach(card => {
      card.addEventListener('click', async () => {
        const emailId = card.dataset.id;
        
        try {
          await fetch(`${API_URL}/api/mark-read`, {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'Authorization': `Bearer ${token}`
            },
            body: JSON.stringify({ email_id: emailId })
          });
          
          card.style.opacity = '0.5';
          setTimeout(() => {
            card.remove();
            
            currentEmails = currentEmails.filter(e => e.id !== emailId);
            
            if (document.querySelectorAll('.email-card').length === 0) {
              main();
            }
          }, 300);
        } catch (error) {
          console.error('Error marking email as read:', error);
        }
      });
    });

  } catch (error) {
    loading.style.display = 'none';
    emailsContainer.style.display = 'block';
    emailsContainer.innerHTML = `
      <div class="error">
        <strong>Client-Side Error:</strong> ${error.message}
        <br><br>
        <small>If this is a token error, please close the popup and try again.</small>
      </div>
    `;
    refreshBtn.style.display = 'block';
    refreshBtn.disabled = false;
  }
}

async function askQuestion(question) {
  const chatMessages = document.getElementById('chatMessages');
  const chatInput = document.getElementById('chatInput');
  const chatSendBtn = document.getElementById('chatSendBtn');

  if (!question.trim()) return;

  chatMessages.classList.add('active');

  const userMessage = document.createElement('div');
  userMessage.className = 'chat-message user';
  userMessage.textContent = question;
  chatMessages.appendChild(userMessage);

  chatInput.value = '';
  chatInput.disabled = true;
  chatSendBtn.disabled = true;

  const loadingMessage = document.createElement('div');
  loadingMessage.className = 'chat-message assistant';
  loadingMessage.textContent = 'Thinking...';
  chatMessages.appendChild(loadingMessage);

  chatMessages.scrollTop = chatMessages.scrollHeight;

  // Log the request payload being sent
  console.log("DEBUG: Sending question to /api/ask-question. Emails count:", currentEmails.length);

  try {
    const response = await fetch(`${API_URL}/api/ask-question`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        question: question,
        emails: currentEmails
      })
    });

    // Check for non-200 responses before parsing JSON
    if (!response.ok) {
        throw new Error(`Server responded with status ${response.status}: ${response.statusText}`);
    }

    const data = await response.json();

    loadingMessage.remove();

    if (data.error) {
      // If the backend returns an error (e.g., API Key missing/invalid)
      const errorMessage = document.createElement('div');
      errorMessage.className = 'chat-message assistant error';
      errorMessage.textContent = `API Error: ${data.error}`;
      chatMessages.appendChild(errorMessage);
    } else {
      const assistantMessage = document.createElement('div');
      assistantMessage.className = 'chat-message assistant';
      assistantMessage.textContent = data.answer;
      chatMessages.appendChild(assistantMessage);
    }

  } catch (error) {
    loadingMessage.remove();
    const errorMessage = document.createElement('div');
    errorMessage.className = 'chat-message assistant error';
    errorMessage.textContent = `Connection error: ${error.message}`;
    chatMessages.appendChild(errorMessage);
  } finally {
    // This MUST ensure the controls are re-enabled
    chatInput.disabled = false;
    chatSendBtn.disabled = false;
    chatInput.focus();
    chatMessages.scrollTop = chatMessages.scrollHeight;
  }
}


function escapeHtml(text) {
  if (typeof text !== 'string') return '';
  const map = {
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '&quot;': '&#039;'
  };
  return text.replace(/[&<>"']/g, m => map[m]);
}

async function main() {
  const loading = document.getElementById('loading');
  const emailsContainer = document.getElementById('emails');
  loading.style.display = 'block';

  try {
    const token = await getAuthToken();
    await fetchEmails(token);
  } catch (error) {
    console.error("DEBUG: Main execution failed.", error);
    loading.style.display = 'none';
    emailsContainer.style.display = 'block';
    emailsContainer.innerHTML = `
      <div class="error">
        <strong>Authentication Required/Failed:</strong> 
        ${error.message}
        <p>Please ensure your <strong>manifest.json</strong> includes the correct <strong>Client ID</strong> and <strong>identity</strong> permission, and then **reinstall** the extension.</p>
      </div>
    `;
    document.getElementById('refreshBtn').style.display = 'none';
  }
}

document.getElementById('refreshBtn').addEventListener('click', main);

document.getElementById('chatSendBtn').addEventListener('click', () => {
  const question = document.getElementById('chatInput').value;
  askQuestion(question);
});

document.getElementById('chatInput').addEventListener('keypress', (e) => {
  if (e.key === 'Enter') {
    const question = document.getElementById('chatInput').value;
    askQuestion(question);
  }
});

document.querySelectorAll('.suggested-question').forEach(btn => {
  btn.addEventListener('click', () => {
    const question = btn.dataset.question;
    document.getElementById('chatInput').value = question;
    askQuestion(question);
  });
});

main();