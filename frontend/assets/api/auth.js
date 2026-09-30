/* Sign-in, current user, and (admin) user management. */
import { get, post, request } from './client.js';

export const status = () => get('/api/auth/status');
export const login = (username, password) => post('/api/auth/login', { username, password });
export const logout = () => post('/api/auth/logout', {});
export const me = () => get('/api/auth/me');
export const changePassword = (payload) => post('/api/auth/password', payload);

export const listUsers = () => get('/api/users');
export const createUser = (payload) => post('/api/users', payload);
export const updateUser = (userId, payload) =>
  request(`/api/users/${encodeURIComponent(userId)}`, { method: 'PATCH', body: payload });
export const deleteUser = (userId) =>
  request(`/api/users/${encodeURIComponent(userId)}`, { method: 'DELETE' });
