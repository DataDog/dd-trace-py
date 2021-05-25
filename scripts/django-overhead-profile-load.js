import http from 'k6/http';
import { sleep } from 'k6';

export const options = {
    duration: '10s',
    vus: 50,
};

export default function () {
    const res = http.get('http://127.0.0.1:8000/accounts/signup/');
    sleep(1);
}
