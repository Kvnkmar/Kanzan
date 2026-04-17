/**
 * VoIP Softphone — Browser-based SIP phone using SIP.js + Asterisk WebRTC.
 *
 * Handles:
 *  - SIP registration with Asterisk via WSS
 *  - Outbound/inbound call management
 *  - Call controls: hold, mute, transfer, DTMF
 *  - Real-time call state via Django Channels WebSocket
 *  - Contact name resolution via API
 */

(function () {
  'use strict';

  // -----------------------------------------------------------------------
  // State
  // -----------------------------------------------------------------------

  let userAgent = null;
  let currentSession = null;
  let callTimer = null;
  let callStartTime = null;
  let isMuted = false;
  let isOnHold = false;
  let currentCallLogId = null;
  let sipRegistered = false;
  let eventSocket = null;

  // DOM refs (populated on init)
  const el = {};

  // -----------------------------------------------------------------------
  // Initialization
  // -----------------------------------------------------------------------

  function init() {
    // Cache DOM elements
    el.toggle = document.getElementById('softphoneToggle');
    el.panel = document.getElementById('softphonePanel');
    el.dialInput = document.getElementById('dialInput');
    el.dialCallBtn = document.getElementById('dialCallBtn');
    el.dialBackspace = document.getElementById('dialBackspace');
    el.sipStatusDot = document.getElementById('sipStatusDot');
    el.sipStatusLabel = document.getElementById('sipStatusLabel');
    el.dialPadView = document.getElementById('dialPadView');
    el.activeCallView = document.getElementById('activeCallView');
    el.transferView = document.getElementById('transferView');
    el.callStatusLabel = document.getElementById('callStatusLabel');
    el.callContactName = document.getElementById('callContactName');
    el.callNumber = document.getElementById('callNumber');
    el.callTimerEl = document.getElementById('callTimer');
    el.muteBtn = document.getElementById('muteBtn');
    el.holdBtn = document.getElementById('holdBtn');
    el.dtmfBtn = document.getElementById('dtmfBtn');
    el.transferBtn = document.getElementById('transferBtn');
    el.hangupBtn = document.getElementById('hangupBtn');
    el.dtmfPad = document.getElementById('dtmfPad');
    el.transferInput = document.getElementById('transferInput');
    el.transferConfirmBtn = document.getElementById('transferConfirmBtn');
    el.transferBack = document.getElementById('transferBack');
    el.incomingModal = document.getElementById('incomingCallModal');
    el.incomingCallerName = document.getElementById('incomingCallerName');
    el.incomingCallerNumber = document.getElementById('incomingCallerNumber');
    el.incomingAccept = document.getElementById('incomingAccept');
    el.incomingReject = document.getElementById('incomingReject');
    el.remoteAudio = document.getElementById('softphoneRemoteAudio');

    if (!el.toggle) return; // Widget not in DOM

    bindEvents();
    fetchCredentialsAndRegister();
    connectEventSocket();

    el.toggle.style.display = '';
  }

  // -----------------------------------------------------------------------
  // Event binding
  // -----------------------------------------------------------------------

  function bindEvents() {
    // Toggle panel
    el.toggle.addEventListener('click', togglePanel);
    document.getElementById('softphoneMinimize').addEventListener('click', togglePanel);
    document.getElementById('softphoneClose').addEventListener('click', togglePanel);

    // Dial pad keys
    document.querySelectorAll('.softphone-key[data-digit]').forEach(function (key) {
      key.addEventListener('click', function () {
        el.dialInput.value += this.dataset.digit;
        el.dialInput.focus();
      });
    });

    // DTMF keys
    document.querySelectorAll('.softphone-key[data-dtmf]').forEach(function (key) {
      key.addEventListener('click', function () {
        sendDTMF(this.dataset.dtmf);
      });
    });

    // Backspace
    el.dialBackspace.addEventListener('click', function () {
      el.dialInput.value = el.dialInput.value.slice(0, -1);
    });

    // Call button
    el.dialCallBtn.addEventListener('click', initiateCall);
    el.dialInput.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') initiateCall();
    });

    // Call controls
    el.muteBtn.addEventListener('click', toggleMute);
    el.holdBtn.addEventListener('click', toggleHold);
    el.dtmfBtn.addEventListener('click', toggleDTMFPad);
    el.transferBtn.addEventListener('click', showTransferView);
    el.hangupBtn.addEventListener('click', hangupCall);

    // Transfer
    el.transferBack.addEventListener('click', hideTransferView);
    el.transferConfirmBtn.addEventListener('click', confirmTransfer);

    // Incoming call
    el.incomingAccept.addEventListener('click', acceptIncomingCall);
    el.incomingReject.addEventListener('click', rejectIncomingCall);

    // Click-to-call support
    document.addEventListener('click', function (e) {
      var callBtn = e.target.closest('[data-voip-call]');
      if (callBtn) {
        e.preventDefault();
        var number = callBtn.dataset.voipCall;
        var contactId = callBtn.dataset.contactId || null;
        var ticketId = callBtn.dataset.ticketId || null;
        dialNumber(number, contactId, ticketId);
      }
    });
  }

  // -----------------------------------------------------------------------
  // Panel visibility
  // -----------------------------------------------------------------------

  function togglePanel() {
    var isVisible = el.panel.style.display !== 'none';
    el.panel.style.display = isVisible ? 'none' : '';
    if (!isVisible) el.dialInput.focus();
  }

  // -----------------------------------------------------------------------
  // SIP Registration
  // -----------------------------------------------------------------------

  async function fetchCredentialsAndRegister() {
    try {
      var resp = await Api.get('/api/v1/voip/sip-credentials/');
      if (resp && resp.sip_uri) {
        registerSIP(resp);
      } else {
        setSIPStatus('offline', 'No Extension');
      }
    } catch (err) {
      console.warn('VoIP: Could not fetch SIP credentials:', err);
      setSIPStatus('offline', 'Not Available');
    }
  }

  function registerSIP(creds) {
    if (typeof SIP === 'undefined') {
      console.warn('VoIP: SIP.js not loaded');
      setSIPStatus('offline', 'Error');
      return;
    }

    var uri = SIP.UserAgent.makeURI(creds.sip_uri);
    if (!uri) {
      console.error('VoIP: Invalid SIP URI:', creds.sip_uri);
      setSIPStatus('offline', 'Error');
      return;
    }

    var transportOptions = {
      server: creds.wss_url,
      traceSip: false,
    };

    var iceServers = [];
    if (creds.stun_servers) {
      creds.stun_servers.forEach(function (s) {
        iceServers.push({ urls: s });
      });
    }
    if (creds.turn_servers) {
      creds.turn_servers.forEach(function (t) {
        iceServers.push({
          urls: t.urls,
          username: t.username,
          credential: t.credential,
        });
      });
    }

    userAgent = new SIP.UserAgent({
      uri: uri,
      transportOptions: transportOptions,
      authorizationUsername: creds.sip_uri.split(':')[1].split('@')[0],
      authorizationPassword: creds.sip_password,
      sessionDescriptionHandlerFactoryOptions: {
        peerConnectionConfiguration: {
          iceServers: iceServers,
        },
      },
      delegate: {
        onInvite: handleIncomingCall,
      },
    });

    var registerer = new SIP.Registerer(userAgent);

    userAgent.start().then(function () {
      registerer.register();
      setSIPStatus('registering', 'Registering...');

      registerer.stateChange.addListener(function (state) {
        switch (state) {
          case SIP.RegistererState.Registered:
            sipRegistered = true;
            setSIPStatus('online', 'Ready');
            break;
          case SIP.RegistererState.Unregistered:
            sipRegistered = false;
            setSIPStatus('offline', 'Offline');
            break;
          default:
            setSIPStatus('registering', 'Connecting...');
        }
      });
    }).catch(function (err) {
      console.error('VoIP: SIP UA start failed:', err);
      setSIPStatus('offline', 'Error');
    });
  }

  function setSIPStatus(state, label) {
    el.sipStatusDot.className = 'softphone-status-dot softphone-status-' + state;
    el.sipStatusLabel.textContent = label;
    el.sipStatusLabel.className = 'softphone-sip-status softphone-sip-' + state;
  }

  // -----------------------------------------------------------------------
  // Outbound calls
  // -----------------------------------------------------------------------

  function dialNumber(number, contactId, ticketId) {
    el.dialInput.value = number;
    el.panel.style.display = '';
    initiateCall(contactId, ticketId);
  }

  async function initiateCall(contactId, ticketId) {
    var number = el.dialInput.value.trim();
    if (!number) return;

    if (!sipRegistered) {
      Toast.error('Softphone is not connected. Please wait for registration.');
      return;
    }

    // Show active call view
    showActiveCallView(number, 'Calling...');

    try {
      // Create call log via API
      var payload = { callee_number: number };
      if (contactId) payload.contact_id = contactId;
      if (ticketId) payload.ticket_id = ticketId;

      var resp = await Api.post('/api/v1/voip/calls/initiate/', payload);
      currentCallLogId = resp.id;

      // Initiate SIP call via SIP.js
      var target = SIP.UserAgent.makeURI('sip:' + number + '@' + userAgent.configuration.uri.host);
      if (!target) {
        Toast.error('Invalid phone number');
        showDialPadView();
        return;
      }

      var inviter = new SIP.Inviter(userAgent, target);
      currentSession = inviter;

      setupSessionHandlers(inviter);

      await inviter.invite({
        requestDelegate: {
          onProgress: function () {
            updateCallStatus('Ringing...');
          },
          onAccept: function () {
            updateCallStatus('Connected');
            startCallTimer();
          },
          onReject: function () {
            updateCallStatus('Rejected');
            endCall();
          },
        },
      });

    } catch (err) {
      console.error('VoIP: Call initiation failed:', err);
      Toast.error('Failed to initiate call');
      showDialPadView();
    }
  }

  // -----------------------------------------------------------------------
  // Incoming calls
  // -----------------------------------------------------------------------

  function handleIncomingCall(invitation) {
    var callerUri = invitation.remoteIdentity.uri.toString();
    var callerDisplay = invitation.remoteIdentity.displayName || callerUri;

    el.incomingCallerName.textContent = callerDisplay;
    el.incomingCallerNumber.textContent = callerUri;
    el.incomingModal.style.display = '';

    // Play ringtone via Web Audio API
    playRingtone();

    // Store the invitation for accept/reject
    el.incomingModal._invitation = invitation;

    // Auto-dismiss if caller hangs up
    invitation.stateChange.addListener(function (state) {
      if (state === SIP.SessionState.Terminated) {
        hideIncomingModal();
      }
    });
  }

  function acceptIncomingCall() {
    var invitation = el.incomingModal._invitation;
    if (!invitation) return;

    hideIncomingModal();
    currentSession = invitation;
    setupSessionHandlers(invitation);

    invitation.accept();
    var callerNumber = invitation.remoteIdentity.uri.user || 'Unknown';
    showActiveCallView(callerNumber, 'Connected');
    startCallTimer();
  }

  function rejectIncomingCall() {
    var invitation = el.incomingModal._invitation;
    if (invitation) {
      invitation.reject();
    }
    hideIncomingModal();
  }

  function hideIncomingModal() {
    el.incomingModal.style.display = 'none';
    el.incomingModal._invitation = null;
    stopRingtone();
  }

  // -----------------------------------------------------------------------
  // Session handlers
  // -----------------------------------------------------------------------

  function setupSessionHandlers(session) {
    session.stateChange.addListener(function (state) {
      switch (state) {
        case SIP.SessionState.Established:
          updateCallStatus('Connected');
          startCallTimer();
          // Attach remote audio
          var receivers = session.sessionDescriptionHandler.peerConnection.getReceivers();
          if (receivers.length > 0) {
            var stream = new MediaStream();
            receivers.forEach(function (r) {
              if (r.track) stream.addTrack(r.track);
            });
            el.remoteAudio.srcObject = stream;
          }
          break;
        case SIP.SessionState.Terminated:
          endCall();
          break;
      }
    });
  }

  // -----------------------------------------------------------------------
  // Call controls
  // -----------------------------------------------------------------------

  function toggleMute() {
    if (!currentSession) return;
    isMuted = !isMuted;

    if (currentSession.sessionDescriptionHandler) {
      var pc = currentSession.sessionDescriptionHandler.peerConnection;
      pc.getSenders().forEach(function (sender) {
        if (sender.track && sender.track.kind === 'audio') {
          sender.track.enabled = !isMuted;
        }
      });
    }

    el.muteBtn.classList.toggle('active', isMuted);
    el.muteBtn.querySelector('i').className = isMuted
      ? 'ti ti-microphone-off'
      : 'ti ti-microphone';
    el.muteBtn.querySelector('span').textContent = isMuted ? 'Unmute' : 'Mute';
  }

  async function toggleHold() {
    if (!currentSession || !currentCallLogId) return;

    try {
      await Api.post('/api/v1/voip/calls/' + currentCallLogId + '/hold/');
      isOnHold = !isOnHold;

      el.holdBtn.classList.toggle('active', isOnHold);
      el.holdBtn.querySelector('i').className = isOnHold
        ? 'ti ti-player-play'
        : 'ti ti-player-pause';
      el.holdBtn.querySelector('span').textContent = isOnHold ? 'Resume' : 'Hold';
      updateCallStatus(isOnHold ? 'On Hold' : 'Connected');
    } catch (err) {
      console.error('VoIP: Hold toggle failed:', err);
      Toast.error('Failed to toggle hold');
    }
  }

  function toggleDTMFPad() {
    var isVisible = el.dtmfPad.style.display !== 'none';
    el.dtmfPad.style.display = isVisible ? 'none' : '';
    el.dtmfBtn.classList.toggle('active', !isVisible);
  }

  function sendDTMF(digit) {
    if (!currentSession || !currentSession.sessionDescriptionHandler) return;

    var pc = currentSession.sessionDescriptionHandler.peerConnection;
    var sender = pc.getSenders().find(function (s) {
      return s.track && s.track.kind === 'audio';
    });

    if (sender && sender.dtmf) {
      sender.dtmf.insertDTMF(digit, 100, 70);
    }
  }

  async function hangupCall() {
    if (currentSession) {
      try {
        if (currentSession.state === SIP.SessionState.Established) {
          currentSession.bye();
        } else {
          currentSession.cancel();
        }
      } catch (e) {
        console.warn('VoIP: Session cleanup error:', e);
      }
    }

    if (currentCallLogId) {
      try {
        await Api.post('/api/v1/voip/calls/' + currentCallLogId + '/hangup/');
      } catch (e) {
        // Call may already be ended on server
      }
    }

    endCall();
  }

  function showTransferView() {
    el.activeCallView.style.display = 'none';
    el.transferView.style.display = '';
    el.transferInput.value = '';
    el.transferInput.focus();
  }

  function hideTransferView() {
    el.transferView.style.display = 'none';
    el.activeCallView.style.display = '';
  }

  async function confirmTransfer() {
    var target = el.transferInput.value.trim();
    if (!target || !currentCallLogId) return;

    try {
      await Api.post('/api/v1/voip/calls/' + currentCallLogId + '/transfer/', {
        target_number: target,
      });
      Toast.success('Call transferred');
      endCall();
    } catch (err) {
      console.error('VoIP: Transfer failed:', err);
      Toast.error('Transfer failed');
      hideTransferView();
    }
  }

  // -----------------------------------------------------------------------
  // Call state management
  // -----------------------------------------------------------------------

  function showActiveCallView(number, statusText) {
    el.dialPadView.style.display = 'none';
    el.transferView.style.display = 'none';
    el.activeCallView.style.display = '';

    el.callNumber.textContent = number;
    el.callContactName.textContent = '';
    el.callStatusLabel.textContent = statusText;
    el.callTimerEl.textContent = '00:00';

    isMuted = false;
    isOnHold = false;
    el.muteBtn.classList.remove('active');
    el.holdBtn.classList.remove('active');
    el.dtmfPad.style.display = 'none';
    el.dtmfBtn.classList.remove('active');

    // Resolve contact name
    resolveContactName(number);
  }

  function showDialPadView() {
    el.activeCallView.style.display = 'none';
    el.transferView.style.display = 'none';
    el.dialPadView.style.display = '';
    el.dialInput.value = '';
  }

  function updateCallStatus(text) {
    el.callStatusLabel.textContent = text;
  }

  function startCallTimer() {
    callStartTime = Date.now();
    callTimer = setInterval(function () {
      var elapsed = Math.floor((Date.now() - callStartTime) / 1000);
      var mins = Math.floor(elapsed / 60).toString().padStart(2, '0');
      var secs = (elapsed % 60).toString().padStart(2, '0');
      el.callTimerEl.textContent = mins + ':' + secs;
    }, 1000);
  }

  function stopCallTimer() {
    if (callTimer) {
      clearInterval(callTimer);
      callTimer = null;
    }
    callStartTime = null;
  }

  function endCall() {
    stopCallTimer();
    currentSession = null;
    currentCallLogId = null;
    isMuted = false;
    isOnHold = false;
    el.remoteAudio.srcObject = null;
    showDialPadView();
  }

  // -----------------------------------------------------------------------
  // Contact name resolution
  // -----------------------------------------------------------------------

  async function resolveContactName(number) {
    try {
      var resp = await Api.get('/api/v1/contacts/contacts/?search=' + encodeURIComponent(number) + '&page_size=1');
      if (resp && resp.results && resp.results.length > 0) {
        var contact = resp.results[0];
        el.callContactName.textContent = contact.first_name + ' ' + contact.last_name;
      }
    } catch (e) {
      // Silently fail — number display is sufficient
    }
  }

  // -----------------------------------------------------------------------
  // Ringtone (Web Audio API)
  // -----------------------------------------------------------------------

  let ringtoneCtx = null;
  let ringtoneOsc = null;

  function playRingtone() {
    try {
      ringtoneCtx = new (window.AudioContext || window.webkitAudioContext)();
      ringtoneOsc = ringtoneCtx.createOscillator();
      var gain = ringtoneCtx.createGain();

      ringtoneOsc.type = 'sine';
      ringtoneOsc.frequency.value = 440;
      gain.gain.value = 0.1;

      ringtoneOsc.connect(gain);
      gain.connect(ringtoneCtx.destination);
      ringtoneOsc.start();
    } catch (e) {
      // Audio may be blocked by browser autoplay policy
    }
  }

  function stopRingtone() {
    if (ringtoneOsc) {
      try { ringtoneOsc.stop(); } catch (e) {}
      ringtoneOsc = null;
    }
    if (ringtoneCtx) {
      try { ringtoneCtx.close(); } catch (e) {}
      ringtoneCtx = null;
    }
  }

  // -----------------------------------------------------------------------
  // Real-time event WebSocket
  // -----------------------------------------------------------------------

  function connectEventSocket() {
    var protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    var wsUrl = protocol + '//' + window.location.host + '/ws/voip/events/';

    eventSocket = new WebSocket(wsUrl);

    eventSocket.onopen = function () {
      console.log('VoIP: Event WebSocket connected');
    };

    eventSocket.onmessage = function (e) {
      try {
        var data = JSON.parse(e.data);
        handleCallEvent(data);
      } catch (err) {
        console.warn('VoIP: Invalid event data:', e.data);
      }
    };

    eventSocket.onclose = function (e) {
      console.log('VoIP: Event WebSocket closed, reconnecting in 5s...');
      setTimeout(connectEventSocket, 5000);
    };

    eventSocket.onerror = function (err) {
      console.error('VoIP: Event WebSocket error:', err);
    };
  }

  function handleCallEvent(data) {
    if (!data || !data.call) return;

    var call = data.call;

    switch (data.type) {
      case 'call_ringing':
        if (currentCallLogId === call.id) {
          updateCallStatus('Ringing...');
        }
        break;
      case 'call_answered':
        if (currentCallLogId === call.id) {
          updateCallStatus('Connected');
        }
        break;
      case 'call_ended':
        if (currentCallLogId === call.id) {
          endCall();
        }
        break;
      case 'call_hold':
        if (currentCallLogId === call.id) {
          updateCallStatus('On Hold');
        }
        break;
    }
  }

  // -----------------------------------------------------------------------
  // Public API
  // -----------------------------------------------------------------------

  window.VoIPSoftphone = {
    dial: dialNumber,
    hangup: hangupCall,
    togglePanel: togglePanel,
    isRegistered: function () { return sipRegistered; },
    isInCall: function () { return currentSession !== null; },
  };

  // Initialize on DOM ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
