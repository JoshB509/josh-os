'use strict';
(() => {
  const button = document.querySelector('#request');
  const output = document.querySelector('#code');
  const status = document.querySelector('#status');
  let timer;
  async function post(path, body) {
    const response = await fetch(path, {method:'POST', credentials:'same-origin',
      headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Pairing is unavailable.');
    return result;
  }
  function stop(message) {
    clearTimeout(timer);
    status.textContent = message;
    button.disabled = false;
    button.textContent = 'Request a new code';
  }
  button.addEventListener('click', async () => {
    clearTimeout(timer);
    button.disabled = true;
    output.textContent = '';
    status.textContent = 'Requesting a code…';
    try {
      const request = await post('/api/pairing/request', {});
      output.textContent = request.code;
      status.textContent = 'Send this code to your dot chat. Waiting for local approval…';
      const deadline = Date.now() + 300000;
      async function poll() {
        if (Date.now() >= deadline) return stop('This request expired. Request a new code.');
        try {
          const result = await post('/api/pairing/poll', {code:request.code});
          if (result.state === 'paired') {
            status.textContent = 'Browser paired. Opening JoshOS…';
            location.replace('/');
          } else if (result.state === 'pending') {
            timer = setTimeout(poll, 5000);
          } else {
            stop('This request is no longer available. Request a new code in this browser.');
          }
        } catch (error) { stop(error.message); }
      }
      timer = setTimeout(poll, 5000);
    } catch (error) { stop(error.message); }
  });
})();
