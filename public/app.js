const socket = io();
let currentUser = null;
let peerConnection;
let localStream;

const config = { iceServers: [{ urls: 'stun:stun.l.google.com:19302' }] };

// LOGIN
async function login() {
  const username = document.getElementById('authUsername').value;
  const password = document.getElementById('authPassword').value;

  const res = await fetch('/api/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password })
  });
  const data = await res.json();

  if (data.token) {
    localStorage.setItem('token', data.token);
    currentUser = data.user;
    initApp();
  } else {
    alert(data.error);
  }
}

// SIGNUP
async function signup() {
  const username = document.getElementById('authUsername').value;
  const password = document.getElementById('authPassword').value;
  const full_name = document.getElementById('authName').value;
  const email = document.getElementById('authEmail').value;

  const res = await fetch('/api/signup', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password, full_name, email })
  });
  const data = await res.json();
  alert(data.message || data.error);
}

function initApp() {
  document.getElementById('authBox').style.display = 'none';
  document.getElementById('appBody').style.display = 'grid';
  document.getElementById('logoutBtn').style.display = 'block';

  document.getElementById('userName').innerText = currentUser.full_name;
  document.getElementById('userImg').src = currentUser.profile_pic;

  socket.emit('register_user', currentUser.id);

  if (currentUser.role === 'admin') {
    document.getElementById('adminPanel').style.display = 'block';
    loadPendingPosts();
  }

  loadUsers();
  loadPosts();
  loadNotifications();
}

// 3. EDIT PROFILE
async function editProfile() {
  const full_name = prompt("Maqaa kee haaraa:", currentUser.full_name);
  const bio = prompt("Bio kee:", "");
  const profile_pic = prompt("URL Fakki Profile:", currentUser.profile_pic);

  if (full_name) {
    await fetch('/api/profile', {
      method: 'PUT',
      headers: { 
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${localStorage.getItem('token')}`
      },
      body: JSON.stringify({ full_name, bio, profile_pic })
    });
    location.reload();
  }
}

// 4. CREATE POST
async function createPost() {
  const content = document.getElementById('postContent').value;
  const media_url = document.getElementById('mediaUrl').value;
  const media_type = document.getElementById('mediaType').value;

  const res = await fetch('/api/posts', {
    method: 'POST',
    headers: { 
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${localStorage.getItem('token')}`
    },
    body: JSON.stringify({ content, media_url, media_type })
  });

  const data = await res.json();
  alert(data.message); // Maammilli ERGAA POST TA'EE JIRA QOFA ARGA!
  document.getElementById('postContent').value = '';
}

// LOAD POSTS WITH DOWNLOAD OPTION
async function loadPosts() {
  const res = await fetch('/api/posts');
  const posts = await res.json();
  const feed = document.getElementById('postsFeed');
  feed.innerHTML = '';

  posts.forEach(p => {
    let mediaHtml = '';
    if (p.media_type === 'image') {
      mediaHtml = `<img src="${p.media_url}" style="width:100%">`;
    } else if (p.media_type === 'video') {
      mediaHtml = `
        <video controls src="${p.media_url}"></video>
        <a class="download-btn" href="${p.media_url}" download target="_blank">📥 Video Buufadhu (Download)</a>
      `;
    } else if (p.media_type === 'audio') {
      mediaHtml = `
        <audio controls src="${p.media_url}"></audio>
        <a class="download-btn" href="${p.media_url}" download target="_blank">📥 Sagalee Buufadhu (Download)</a>
      `;
    }

    feed.innerHTML += `
      <div class="card">
        <b>${p.username}</b>
        <p>${p.content}</p>
        ${mediaHtml}
        <br><br>
        <button onclick="alert('Comment ergameera!')">Comment</button>
        <button onclick="alert('Follow godhteetta!')">Follow</button>
      </div>
    `;
  });
}

// LOAD USERS (ONLINE / OFFLINE CHAT & CALL)
async function loadUsers() {
  const res = await fetch('/api/users', {
    headers: { 'Authorization': `Bearer ${localStorage.getItem('token')}` }
  });
  const users = await res.json();
  const list = document.getElementById('usersList');
  list.innerHTML = '';

  users.forEach(u => {
    if (u.id !== currentUser.id) {
      list.innerHTML += `
        <li>
          <span class="status-dot ${u.is_online ? 'online' : 'offline'}"></span>
          ${u.full_name} (${u.is_online ? 'Online' : 'Offline'})
          <button onclick="startCall(${u.id})">📞 Video Call</button>
        </li>
      `;
    }
  });
}

// NOTIFICATIONS & MISSED CALLS
async function loadNotifications() {
  const res = await fetch('/api/notifications', {
    headers: { 'Authorization': `Bearer ${localStorage.getItem('token')}` }
  });
  const notifs = await res.json();
  const box = document.getElementById('notifications');
  if (notifs.length > 0) {
    box.innerHTML = '<h4>Beeksisa & Missed Calls:</h4>';
    notifs.forEach(n => {
      box.innerHTML += `<p>🔔 ${n.message} <small>(${new Date(n.created_at).toLocaleTimeString()})</small></p>`;
    });
  }
}

// WEBRTC VIDEO CALL ENGINE
async function startCall(toUserId) {
  document.getElementById('callModal').style.display = 'flex';
  localStream = await navigator.mediaDevices.getUserMedia({ video: true, audio: true });
  document.getElementById('localVideo').srcObject = localStream;

  peerConnection = new RTCPeerConnection(config);
  localStream.getTracks().forEach(track => peerConnection.addTrack(track, localStream));

  peerConnection.onicecandidate = e => {
    if (e.candidate) socket.emit('ice_candidate', { toUserId, candidate: e.candidate });
  };

  peerConnection.ontrack = e => {
    document.getElementById('remoteVideo').srcObject = e.streams[0];
  };

  const offer = await peerConnection.createOffer();
  await peerConnection.setLocalDescription(offer);

  socket.emit('call_user', { toUserId, offer, callerName: currentUser.full_name });
}

socket.on('incoming_call', async ({ from, offer, callerName }) => {
  document.getElementById('callModal').style.display = 'flex';
  document.getElementById('callerNameText').innerText = `${callerName} siif bilbilaa jira...`;

  document.getElementById('acceptCallBtn').onclick = async () => {
    localStream = await navigator.mediaDevices.getUserMedia({ video: true, audio: true });
    document.getElementById('localVideo').srcObject = localStream;

    peerConnection = new RTCPeerConnection(config);
    localStream.getTracks().forEach(track => peerConnection.addTrack(track, localStream));

    peerConnection.onicecandidate = e => {
      if (e.candidate) socket.emit('ice_candidate', { toUserId: from, candidate: e.candidate });
    };

    peerConnection.ontrack = e => {
      document.getElementById('remoteVideo').srcObject = e.streams[0];
    };

    await peerConnection.setRemoteDescription(offer);
    const answer = await peerConnection.createAnswer();
    await peerConnection.setLocalDescription(answer);

    socket.emit('answer_call', { toUserId: from, answer });
  };
});

socket.on('call_answered', async ({ answer }) => {
  await peerConnection.setRemoteDescription(answer);
});

socket.on('ice_candidate', async ({ candidate }) => {
  if (peerConnection) await peerConnection.addIceCandidate(candidate);
});

socket.on('user_offline', (data) => alert(data.message));

function endCall() {
  if (peerConnection) peerConnection.close();
  document.getElementById('callModal').style.display = 'none';
}

// ADMIN FUNCTIONS
async function loadPendingPosts() {
  const res = await fetch('/api/admin/pending-posts', {
    headers: { 'Authorization': `Bearer ${localStorage.getItem('token')}` }
  });
  const posts = await res.json();
  const box = document.getElementById('pendingPosts');
  box.innerHTML = '';

  posts.forEach(p => {
    box.innerHTML += `
      <div style="border-bottom:1px solid #ccc; padding:5px;">
        <p><b>${p.username}:</b> ${p.content}</p>
        <button onclick="approvePost(${p.id}, 'approved')">Approve</button>
        <button onclick="approvePost(${p.id}, 'rejected')" style="background:red;">Reject</button>
        <button onclick="warnUser(${p.user_id})" style="background:orange;">Adabi (Warn)</button>
      </div>
    `;
  });
}

async function approvePost(post_id, action) {
  await fetch('/api/admin/approve-post', {
    method: 'POST',
    headers: { 
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${localStorage.getItem('token')}`
    },
    body: JSON.stringify({ post_id, action })
  });
  loadPendingPosts();
  loadPosts();
}

async function warnUser(user_id) {
  const days = prompt("Guyyaa meeqaf adabama (Suspended)?", "3");
  const reason = prompt("Sababa adabbii:", "Qabiyyee seeraan ala posti gochuu");
  if (days) {
    await fetch('/api/admin/warn-suspend', {
      method: 'POST',
      headers: { 
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${localStorage.getItem('token')}`
      },
      body: JSON.stringify({ user_id, days: parseInt(days), reason })
    });
    alert("Maammilli adabameera!");
  }
}

function logout() {
  localStorage.clear();
  location.reload();
}
