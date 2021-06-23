import http from 'k6/http';
import { sleep } from 'k6';

export let options = {
    vus: 1,
    iterations: 1000,
    tags: {
        sirun_name: `${__ENV.SIRUN_NAME}`,
        sirun_variant: `${__ENV.SIRUN_VARIANT}`,
        sirun_iteration: `${__ENV.SIRUN_ITERATION}`
    }
};

export default function () {
    const res = http.get('http://127.0.0.1:5000/accounts/signup/');
}
