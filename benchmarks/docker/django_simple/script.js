import http from 'k6/http';
import { sleep } from 'k6';

export default function () {
    const res = http.get('http://127.0.0.1:5000/accounts/signup/');
}
