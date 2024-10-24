import http from 'k6/http';

export const options = {
    duration: '60s',
    vus: 100,
};

export default function () {
    const res = http.get('http://127.0.0.1:8000/users/johndoe/');
}
