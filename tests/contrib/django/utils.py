from zeep import Client
from zeep.transports import Transport


def make_soap_request(url):
    client = Client(wsdl=url, transport=Transport())

    # Call the SOAP service
    response = client.service.EmployeeLeaveStatus(LeaveID="124", Description="Annual leave")
    # Print the response
    print(f"Success: {response.success}")
    print(f"ErrorText: {response.errorText}")

    return response
